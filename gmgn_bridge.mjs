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
try {
  let input='';
  for await (const part of process.stdin) input+=part;
  const request=JSON.parse(input);
  const leaderDays=request.leader_days??50;
  const leaderCap=request.leader_cap??30000000;
  if(!Number.isInteger(leaderDays)||leaderDays<1||leaderDays>3650||!Number.isFinite(leaderCap)||leaderCap<=0||leaderCap>1e12) throw new Error('Invalid leader bounds');
  const {OpenApiClient}=await import(pathToFileURL(join(packageRoot,'dist/client/OpenApiClient.js')).href);
  const {sanitizeForOutputWithCount}=await import(pathToFileURL(join(packageRoot,'dist/sanitize.js')).href);
  const client=new OpenApiClient({apiKey:process.env.GMGN_API_KEY,host:'https://openapi.gmgn.ai'});
  let data;
  if(request.command==='trending' && ['sol','bsc','base','robinhood'].includes(request.chain)) {
    data=await client.getTrendingSwaps(request.chain,'5m',{
      order_by:'volume',direction:'desc',limit:100,filters:['is_internal_market','is_out_market'],
      ...(request.leader_filter ? {max_created:`${leaderDays}d`,max_marketcap:leaderCap} : {})
    });
  } else if(request.command==='hot-searches') {
    const interval=request.hot_interval??'5m';
    if(interval!=='5m') throw new Error('Invalid hot-search interval');
    data=await client.getHotSearches(['sol','bsc','base','robinhood'].map(chain=>({
      label:'hot-search',chain,interval,limit:500,filters:[]
    })));
  } else {throw new Error('Read-only request not allowed');}
  const safe=sanitizeForOutputWithCount(data);
  process.stdout.write(JSON.stringify({ok:true,data:safe.data,sanitized:safe.changed}));
} catch(error) {
  // Structured, sanitized errors only; never emit request headers or API payloads.
  process.stdout.write(JSON.stringify({ok:false,error:{status:error.status||0,
    code:error.apiError||'NETWORK_ERROR',reset_at:error.resetAtUnix||0}}));
  process.exitCode=1;
} finally {await dispatcher.close();}
