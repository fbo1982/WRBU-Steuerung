/* Execute the real frontend with a small DOM and simulated HTTP replies. */
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

class Element {
  constructor() { this.hidden=false;this.disabled=false;this.children=[];this.listeners={};this.textContent='';this._value=''; }
  get value(){return this._value;}
  set value(value){this._value=String(value);}
  append(...children){this.children.push(...children);}
  replaceChildren(...children){this.children=children;}
  addEventListener(name,callback){this.listeners[name]=callback;}
  reportValidity(){return true;}
  scrollIntoView(){}
  querySelectorAll(){return [];}
}

async function setup(probeReply, pollReply) {
  const nodes={};
  const get=id=>nodes[id]??=(new Element());
  get('category-editor').hidden=true;
  const form=get('device-form');
  form.elements=Object.fromEntries(['name','profile','host','port','unit'].map(key=>[key,new Element()]));
  form.reportValidity=()=>{throw new Error('Do not validate unrelated hidden fields');};
  const calls=[];
  const status={version:'0.1.3',ha_ready:false,ha_error:'',timezone:'Europe/Berlin',weather:null,devices:[],
    profiles:{sungrow_sh:'Sungrow SH'},default_categories:[{name:'Alle',months:[1,2,3,4,5,6,7,8,9,10,11,12],normal_soc:25,warning_soc:60}]};
  let polls=0;
  const context=vm.createContext({
    document:{getElementById:get,createElement:()=>new Element(),createTextNode:text=>({textContent:text}),activeElement:null},
    AbortController,structuredClone,Date,console,confirm:()=>true,
    setTimeout:(callback,delay)=>{if(delay===700)setImmediate(callback);return 1;},clearTimeout:()=>{},
    fetch:async (url,options={})=>{
      calls.push({url,options});
      if(url==='api/status')return {ok:true,status:200,json:async()=>structuredClone(status)};
      if(url==='api/probes')return probeReply(nodes);
      if(url.startsWith('api/probes/'))return pollReply(++polls,nodes);
      if(url==='api/devices')return {ok:true,status:200,json:async()=>({id:'saved'})};
      throw new Error('Unexpected URL: '+url);
    },
  });
  const script=fs.readFileSync(path.join(__dirname,'../wrbu_steuerung/wrbu/static/app.js'),'utf8');
  vm.runInContext(script,context);
  await new Promise(setImmediate);
  get('add-device').onclick();
  Object.assign(form.elements.name,{value:'Haus'});
  form.elements.host.value='192.0.2.10';form.elements.port.value='502';form.elements.unit.value='1';
  return {nodes,get,form,calls};
}

const reply=data=>({ok:true,status:200,json:async()=>data});
const queued=()=>reply({id:'job1',state:'queued',stage:'queued',elapsed:0,message:'Warten'});
const success=()=>reply({id:'job1',state:'complete',stage:'complete',proof:'verified-proof',
  result:{success:true,model:'SH25T',serial:'SN001',before:35,after:35,write_attempted:true,write_acknowledged:true}});

test('initial connection failure is visible next to the button, not only above the viewport',async()=>{
  const ui=await setup(()=>({ok:false,status:400,json:async()=>({error:'TCP-Verbindung nicht erreichbar'})}));
  await ui.get('test-device').onclick();
  assert.equal(ui.get('test-result').hidden,false);
  assert.match(ui.get('test-result').textContent,/TCP-Verbindung nicht erreichbar/);
  assert.equal(ui.get('save-device').disabled,true);
  assert.match(ui.get('save-reason').textContent,/Keine erfolgreiche/);
});

test('failed serial read shows its step and keeps saving locked',async()=>{
  const ui=await setup(queued,()=>reply({id:'job1',state:'failed',stage:'serial',proof:null,
    result:{success:false,error:'Seriennummer unlesbar'}}));
  await ui.get('test-device').onclick();
  assert.match(ui.get('test-result').textContent,/Seriennummer lesen/);
  assert.match(ui.get('test-result').textContent,/Seriennummer unlesbar/);
  assert.equal(ui.get('save-device').disabled,true);
});

test('successful verified result unlocks saving with its proof',async()=>{
  const ui=await setup(queued,success);
  await ui.get('test-device').onclick();
  assert.equal(ui.get('test-result').hidden,false);
  assert.match(ui.get('test-result').textContent,/Prüfung erfolgreich/);
  assert.equal(ui.get('save-device').disabled,false);
  await ui.form.onsubmit({preventDefault(){}});
  const save=ui.calls.find(call=>call.url==='api/devices');
  assert.equal(JSON.parse(save.options.body).proof,'verified-proof');
});

test('non-JSON ingress errors remain visible in the test area',async()=>{
  const ui=await setup(queued,()=>({ok:false,status:502,json:async()=>{throw new SyntaxError('HTML');}}));
  await ui.get('test-device').onclick();
  assert.match(ui.get('test-result').textContent,/keine lesbare Antwort.*502/);
  assert.equal(ui.get('save-device').disabled,true);
});

test('changed connection cannot reuse a successful test',async()=>{
  const ui=await setup(queued,(_count,nodes)=>{nodes['device-form'].elements.host.value='192.0.2.11';return success();});
  await ui.get('test-device').onclick();
  assert.match(ui.get('test-result').textContent,/Verbindungsdaten während der Prüfung geändert/);
  assert.equal(ui.get('save-device').disabled,true);
});

test('missing proof cannot unlock saving even if a reply claims success',async()=>{
  const ui=await setup(queued,()=>reply({state:'complete',stage:'complete',proof:null,result:{success:true}}));
  await ui.get('test-device').onclick();
  assert.equal(ui.get('save-device').disabled,true);
  assert.match(ui.get('test-result').textContent,/fehlgeschlagen/);
});

test('polling the running job does not start another Modbus test',async()=>{
  const ui=await setup(queued,count=>count<4?reply({id:'job1',state:'running',stage:'readback',elapsed:count,message:'Rücklesen'}):success());
  await ui.get('test-device').onclick();
  assert.equal(ui.calls.filter(call=>call.url==='api/probes').length,1);
  assert.equal(ui.calls.filter(call=>call.url==='api/probes/job1').length,4);
  assert.equal(ui.get('save-device').disabled,false);
});
