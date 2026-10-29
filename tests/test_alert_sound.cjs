// Isolated execution of the actual player: no browser, speaker, network or DB.
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const html=fs.readFileSync(path.join(__dirname,'..','alert_sound.html'),'utf8');
const script=html.match(/<script>([\s\S]*?)<\/script>/)[1];
let now=1800000000000,channel,click,notes=0;const timers=[];
const button={textContent:'',setAttribute(){},addEventListener(_,fn){click=fn}};
const body={dataset:{}};
class Audio{constructor(){this.state='suspended';this.currentTime=0;this.destination={}}async resume(){this.state='running'}createOscillator(){notes++;return {frequency:{},connect(){},start(){},stop(){}}}createGain(){return {gain:{setValueAtTime(){},linearRampToValueAtTime(){},exponentialRampToValueAtTime(){}},connect(){}}}}
class Channel{constructor(){channel=this}postMessage(){}}
const context={document:{body,getElementById:()=>button},window:{AudioContext:Audio},BroadcastChannel:Channel,sessionStorage:{getItem(){return null},setItem(){}},Date:{now:()=>now},setInterval(){},setTimeout(fn){timers.push(fn)}};
vm.runInNewContext(script,context);
function packet(binance,gmgn,events=[]){channel.onmessage({data:{version:1,latest:{binance,gmgn},events}})}
function event(source,id,age=0){return {source,id,time:now/1000-age}}
function flush(){while(timers.length)timers.shift()()}
(async()=>{
 packet(50,20,[event('binance',50),event('gmgn',20)]);assert.equal(notes,0,'opening must not sound old alarms');
 await click();assert.equal(notes,2,'activation plays only a two-note test');
 packet(50,20,[event('binance',50)]);assert.equal(notes,2);
 packet(51,20,[event('binance',51)]);assert.equal(notes,4);flush();
 packet(51,20,[event('binance',51)]);assert.equal(notes,4,'duplicate ID must stay quiet');
 packet(51,21,[event('gmgn',21)]);assert.equal(notes,7,'GMGN uses three notes');flush();
 packet(52,21,[event('binance',52,121)]);assert.equal(notes,7,'stale backlog must stay quiet');
 await click();packet(53,22,[event('binance',53),event('gmgn',22)]);assert.equal(notes,7,'muted events must stay quiet');
 await click();assert.equal(notes,9);packet(53,22,[event('binance',53)]);assert.equal(notes,9,'unmute never replays skipped alerts');
 now+=6000;packet(54,23,[event('binance',54),event('gmgn',23)]);flush();assert.equal(notes,14,'both sources in one burst are delivered');
 console.log('Audio behavior passed: initial backlog, distinct tones, deduplication, freshness, mute, simultaneous sources.');
})().catch(e=>{console.error(e);process.exitCode=1});
