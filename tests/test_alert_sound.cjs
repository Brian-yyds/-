// Execute the real player with isolated audio/storage: no speakers or live alerts.
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const script=fs.readFileSync(path.join(__dirname,'..','alert_sound.html'),'utf8').match(/<script>([\s\S]*?)<\/script>/)[1];
let now=1800000000000;const shared=new Map();
const storage=map=>({getItem:k=>map.get(k)??null,setItem:(k,v)=>map.set(k,String(v))});
function player(){
 let channel,click,notes=0;const intervals=[];
 const button={textContent:'',setAttribute(){},addEventListener(_,fn){click=fn}},body={dataset:{}};
 class Audio{constructor(){this.state='suspended';this.currentTime=0;this.destination={}}async resume(){this.state='running'}createOscillator(){notes++;return {frequency:{},connect(){},start(){},stop(){}}}createGain(){return {gain:{setValueAtTime(){},linearRampToValueAtTime(){},exponentialRampToValueAtTime(){}},connect(){}}}}
 class Channel{constructor(name){assert.equal(name,'perp-workbench-gmgn-alerts-v2');channel=this}postMessage(){}}
 vm.runInNewContext(script,{document:{body,getElementById:()=>button},window:{AudioContext:Audio},BroadcastChannel:Channel,sessionStorage:storage(new Map()),localStorage:storage(shared),Date:{now:()=>now},setInterval:fn=>intervals.push(fn)});
 return {get notes(){return notes},body,click:()=>click(),async packet(id,events=[],version=2){channel.onmessage({data:{version,latest:{gmgn:id},events}});await Promise.resolve()},async tick(){for(const f of intervals)await f()}};
}
const event=(id,age=0,source='gmgn')=>({source,id,time:now/1000-age});
(async()=>{
 const p=player();await p.packet(20,[event(20)]);await p.click();assert.equal(p.notes,0,'opening/enabling must be silent');
 await p.packet(21,[event(21,0,'binance')]);assert.equal(p.notes,0,'Binance never sounds, even in a v2 packet');
 await p.packet(22,[event(22)]);assert.equal(p.notes,1,'one new GMGN alert gives one short tone');
 now+=6000;await p.packet(20,[event(20)]);await p.packet(22,[event(22)]);assert.equal(p.notes,1,'out-of-order old snapshot must not rewind or replay');
 await p.packet(23,[event(23,121)]);assert.equal(p.notes,1,'old backlog must stay quiet');
 await p.packet(24,[]);assert.equal(p.notes,1,'no visible GMGN alarm must stay quiet');
 await p.packet(999,[event(999)],1);assert.equal(p.notes,1,'legacy mixed-source packets are ignored');
 await p.packet(25,[event(25)]);assert.equal(p.notes,2);
 await p.packet(26,[event(26)]);assert.equal(p.notes,2,'burst waits for cooldown');now+=6000;await p.tick();assert.equal(p.notes,3);
 await p.tick();await p.packet(26,[event(26)]);assert.equal(p.notes,3,'heartbeat and duplicate updates stay quiet');
 await p.click();await p.packet(27,[event(27)]);now+=6000;await p.tick();assert.equal(p.notes,3,'muted updates stay quiet');
 await p.click();await p.packet(27,[event(27)]);assert.equal(p.notes,3,'unmute does not replay');
 const other=player();await other.packet(27);await other.click();await p.packet(28,[event(28)]);await other.packet(28,[event(28)]);
 assert.equal(p.notes,4);assert.equal(other.notes,0,'another tab must not repeat the same alarm');
 assert.equal(p.body.dataset.lastSound,'gmgn');assert.equal(p.body.dataset.lastAlertId,'28');
 console.log('GMGN-only sound passed: silent activation/Binance/legacy/heartbeat/backlog, monotonic dedup, cooldown, mute, cross-tab dedup.');
})().catch(e=>{console.error(e);process.exitCode=1});
