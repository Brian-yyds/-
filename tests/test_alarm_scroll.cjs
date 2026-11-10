// Run the actual history-memory script with detached/replaced DOM lists.
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const scripts=[...fs.readFileSync(path.join(__dirname,'..','signal_scroll.html'),'utf8').matchAll(/<script>([\s\S]*?)<\/script>/g)];
const handlers={},frames=[];let observer,current;
class Element {
 constructor(kind,keys){this.kind=kind;this.keys=keys;this.scrollTop=0;this.isConnected=true;this.attrs={}}
 closest(){return this} getAttribute(name){return name==='data-signal-kind'?this.kind:this.attrs[name]}
 setAttribute(name,value){this.attrs[name]=value} contains(node){return node===this}
 getBoundingClientRect(){return {top:0}}
 querySelectorAll(){return this.keys.map((key,i)=>({getAttribute:()=>key,getBoundingClientRect:()=>({top:i*100-this.scrollTop,bottom:(i+1)*100-this.scrollTop})}))}
}
const document={body:{},addEventListener:(name,fn)=>{handlers[name]=fn},querySelectorAll:()=>current?[current]:[]};
vm.runInNewContext(scripts[1][1],{document,window:{},Element,Map,requestAnimationFrame:fn=>frames.push(fn),
 MutationObserver:class{constructor(fn){observer=fn}observe(){}}});
function flush(){while(frames.length)frames.shift()()}
function read(top){handlers.keydown({target:current});current.scrollTop=top;handlers.scroll({target:current});flush()}
function replace(keys){current.isConnected=false;current=new Element('gmgn',keys);observer([{target:document.body}]);handlers.scroll({target:current});flush()}
current=new Element('gmgn',['a','b','c','d','e','f']);read(240);
observer([{target:document.body}]);flush();assert.equal(current.scrollTop,240,'unrelated page mutations must not interrupt reading');
replace(['a','b','c','d','e','f']);assert.equal(current.scrollTop,240,'replacement reset must not overwrite the saved position');
replace(['NEW','a','b','c','d','e','f']);assert.equal(current.scrollTop,340,'new alarm retains the visible anchor and offset');
replace(['NEW','a','b']);assert.equal(current.scrollTop,240,'missing anchor uses the prior offset (browser clamps to list size)');
read(0);replace(['LATEST','NEW','a','b']);assert.equal(current.scrollTop,0,'Home restores following newest alarms');
assert.equal(current.attrs['data-history-scroll'],'v3');
current=new Element('copy',['first','second','third','fourth']);current.scrollTop=300;
observer([{target:document.body}]);flush();assert.equal(current.scrollTop,0,'new copy table must initially follow newest opening');
read(120);
current.isConnected=false;current=new Element('copy',['NEW','first','second','third','fourth']);
observer([{target:document.body}]);handlers.scroll({target:current});flush();
assert.equal(current.scrollTop,220,'copy opening updates must retain the deliberate reading anchor');
console.log('History scroll passed: user-only recording, unrelated updates, DOM replacement, inserted alarm anchor, removed anchor, Home.');
