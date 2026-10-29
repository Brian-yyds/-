// Reuse the official gmgn-cli API client with an explicit read-only credential.
// This avoids its global dotenv loader overriding the workbench account.
import {createRequire} from 'node:module';
import {pathToFileURL} from 'node:url';
import {dirname, join} from 'node:path';

const packageRoot = dirname(dirname(process.argv[2]));
const require = createRequire(join(packageRoot, 'package.json'));
const {Agent, ProxyAgent, buildConnector, setGlobalDispatcher} = require('undici');
const proxy = process.env.HTTPS_PROXY ?? process.env.https_proxy ?? process.env.HTTP_PROXY ?? process.env.http_proxy;
let dispatcher;
if (proxy && /^socks[45]:/i.test(proxy)) {
  const {SocksClient} = require('socks');
  const tls = require('node:tls');
  const p = new URL(proxy);
  dispatcher = new Agent({connect: async (options, callback) => {
    try {
      const {socket} = await SocksClient.createConnection({
        proxy:{host:p.hostname,port:Number(p.port||1080),type:p.protocol==='socks5:'?5:4},
        command:'connect', destination:{host:options.hostname,port:Number(options.port)},socket_options:{family:4}
      });
      callback(null,tls.connect({socket,servername:options.hostname,rejectUnauthorized:true}));
    } catch(error) {callback(error,null);}
  }});
} else {
  dispatcher = proxy ? new ProxyAgent(proxy) : new Agent({connect:buildConnector({family:4})});
}
setGlobalDispatcher(dispatcher);
// Keep diagnostics to fixed categories and HTTP metadata, never raw errors,
// request URLs/headers or provider response bodies (which may contain secrets).
const nativeFetch=globalThis.fetch;
let httpStatus=0;
let networkCode='';
let requestTimeout=20000;
globalThis.fetch=async (url,options={})=>{httpStatus=0;networkCode='';try{const timeout=AbortSignal.timeout(requestTimeout);const signal=options.signal?AbortSignal.any([options.signal,timeout]):timeout;const response=await nativeFetch(url,{...options,signal});httpStatus=response.status;return response}catch(error){let cause=error;for(let i=0;cause?.cause&&i<5;i++)cause=cause.cause;networkCode=/^[A-Z_0-9]{1,60}$/.test(String(cause?.code||''))?cause.code:'';throw error}};
try {
  let input='';
  for await (const part of process.stdin) input+=part;
  const request=JSON.parse(input);
  const {OpenApiClient}=await import(pathToFileURL(join(packageRoot,'dist/client/OpenApiClient.js')).href);
  const {sanitizeForOutputWithCount}=await import(pathToFileURL(join(packageRoot,'dist/sanitize.js')).href);
  const client=new OpenApiClient({apiKey:process.env.GMGN_API_KEY,host:'https://openapi.gmgn.ai'});
  let data;
  if(request.command==='trending' && ['sol','bsc','base','robinhood'].includes(request.chain)) {
    const extra={};
    if(Number.isFinite(request.max_volume)&&request.max_volume>=0)extra.max_volume=request.max_volume;
    data=await client.getTrendingSwaps(request.chain,'5m',{
      order_by:'volume',direction:'desc',limit:Number.isInteger(request.limit)&&request.limit>=10&&request.limit<=100?request.limit:50,filters:['is_out_market'],...extra,
    });
  } else if(request.command==='info-batch' && Array.isArray(request.tokens) && request.tokens.length>=1 && request.tokens.length<=10) {
    requestTimeout=8000;
    data=[];
    for(const [index,t] of request.tokens.entries()) {
      const valid=['sol','bsc','base','robinhood'].includes(t.chain) && typeof t.address==='string' &&
        (t.chain==='sol' ? /^[1-9A-HJ-NP-Za-km-z]{32,44}$/.test(t.address) : /^0x[0-9a-fA-F]{40}$/.test(t.address));
      if(!valid) throw new Error('Invalid read-only token identity');
      if(index) await new Promise(resolve=>setTimeout(resolve,1100));
      try {
        const info=await client.getTokenInfo(t.chain,t.address);
        data.push({...t,ok:true,data:info,observed:Date.now()/1000});
      } catch(error) {
        if([401,403,429].includes(error.status)||String(error.apiError||'').includes('RATE_LIMIT')) throw error;
        data.push({...t,ok:false});
      }
    }
  } else {throw new Error('Read-only request not allowed');}
  const safe=sanitizeForOutputWithCount(data);
  process.stdout.write(JSON.stringify({ok:true,data:safe.data,sanitized:safe.changed}));
} catch(error) {
  // Structured, sanitized errors only; never emit request headers or API payloads.
  const message=String(error.message||'');
  const category=message.includes('non-JSON response')?'NON_JSON_RESPONSE':message.includes('failed to read response body')?'RESPONSE_BODY_ERROR':/timeout|timed out|aborted/i.test(message)?'TIMEOUT':'NETWORK_ERROR';
  process.stdout.write(JSON.stringify({ok:false,error:{status:error.status||httpStatus||0,
    code:error.apiError||networkCode||category,reset_at:error.resetAtUnix||0}}));
  process.exitCode=1;
} finally {await dispatcher.close();}
