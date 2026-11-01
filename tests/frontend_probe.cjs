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
  setAttribute(name,value){this[name]=value;}
  removeAttribute(name){delete this[name];}
  checkValidity(){return true;}
  reportValidity(){return true;}
  scrollIntoView(){}
  querySelectorAll(selector){
    const all=[];
    const visit=node=>{for(const child of node.children||[]){all.push(child);visit(child);}};
    visit(this);
    if(selector==="input:checked")return all.filter(node=>node.type==="checkbox"&&node.checked);
    return Object.values(this.elements||{});
  }
}

async function setup(probeReply, pollReply, settings={}) {
  const nodes={};
  const get=id=>nodes[id]??=(new Element());
  get('category-editor').hidden=true;
  const form=get('device-form');
  form.elements=Object.fromEntries(['name','profile','host','port','unit'].map(key=>[key,new Element()]));
  form.reportValidity=()=>{throw new Error('Do not validate unrelated hidden fields');};
  const calls=[];
  const status={version:'0.1.5',ha_ready:false,ha_error:'',timezone:'Europe/Berlin',weather:null,devices:[],
    profiles:{sungrow_sh:'Sungrow SH'},default_categories:[{name:'Alle',months:[1,2,3,4,5,6,7,8,9,10,11,12],normal_soc:25,warning_soc:60}]};
  let polls=0, savePayload=null;
  const existing={id:"existing",connection:{name:"Haus",profile:"sungrow_sh",host:"192.0.2.10",port:502,unit:1},
    categories:structuredClone(status.default_categories),check_interval_minutes:0,model:"SH25T",serial:"SN001",
    test_result:{before:25,after:25,success:true},runtime:{current:25}};
  if(settings.existing)status.devices=[existing];
  const context=vm.createContext({
    document:{getElementById:get,createElement:()=>new Element(),createTextNode:text=>({textContent:text}),activeElement:null},
    AbortController,structuredClone,Date,console,confirm:()=>true,
    setTimeout:(callback,delay)=>{if(delay===700)setImmediate(callback);return 1;},clearTimeout:()=>{},
    fetch:async (url,options={})=>{
      calls.push({url,options});
      if(url==='api/status')return {ok:true,status:200,json:async()=>structuredClone(status)};
      if(url==='api/probes')return probeReply(nodes);
      if(url.startsWith('api/probes/'))return pollReply(++polls,nodes);
      if(url==='api/saves'){
        savePayload=JSON.parse(options.body);
        return {ok:true,status:202,json:async()=>({id:'save1',state:'queued',stage:'queued',message:'Warten'})};
      }
      if(url==='api/saves/save1')return {ok:true,status:200,json:async()=>({
        id:'save1',state:'complete',stage:'complete',result:{
          ...existing,id:savePayload.id||'saved',connection:savePayload.connection,categories:savePayload.categories,check_interval_minutes:savePayload.check_interval_minutes,
          save_result:{saved:true,applied:!settings.savePaused,outcome:settings.savePaused?'paused':'written',
            message:settings.savePaused?'Einstellungen gespeichert. SOC-Abgleich pausiert.':'Einstellungen gespeichert. Backup-SOC auf 25 % gesetzt und zurückgelesen.',
            category:'Alle',desired:settings.savePaused?null:25,current:25,warning_level:settings.savePaused?null:'0'}
        }
      })};
      throw new Error('Unexpected URL: '+url);
    },
  });
  const script=fs.readFileSync(path.join(__dirname,'../wrbu_steuerung/wrbu/static/app.js'),'utf8');
  vm.runInContext(script,context);
  await new Promise(setImmediate);
  if(settings.existing)vm.runInContext('openEditor(state.devices[0])',context);
  else get('add-device').onclick();
  Object.assign(form.elements.name,{value:'Haus'});
  form.elements.host.value='192.0.2.10';form.elements.port.value='502';form.elements.unit.value='1';
  return {nodes,get,form,calls,context};
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
  const save=ui.calls.find(call=>call.url==='api/saves');
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

test('existing category changes can be adopted and saved without a new probe',async()=>{
  const ui=await setup(queued,success,{existing:true});
  vm.runInContext('openCategory(0)',ui.context);
  ui.get('normal-soc').value='40';
  ui.get('apply-category').onclick();
  assert.equal(ui.get('save-device').disabled,false);
  await ui.form.onsubmit({preventDefault(){}});
  const save=ui.calls.find(call=>call.url==='api/saves');
  assert.ok(save);
  assert.equal(JSON.parse(save.options.body).proof,null);
  assert.equal(JSON.parse(save.options.body).categories[0].normal_soc,40);
  assert.equal(ui.calls.filter(call=>call.url==='api/probes').length,0);
  assert.match(ui.get('save-result').textContent,/gespeichert/);
  assert.match(ui.get('save-result').textContent,/25 %/);
  assert.equal(ui.get('save-result').hidden,false);
});

test('changed existing endpoint still blocks saving without a new probe',async()=>{
  const ui=await setup(queued,success,{existing:true});
  ui.form.elements.port.value='503';
  ui.form.elements.port.listeners.input({});
  assert.equal(ui.get('save-device').disabled,true);
  await ui.form.onsubmit({preventDefault(){}});
  assert.equal(ui.calls.filter(call=>call.url==='api/saves').length,0);
  assert.match(ui.get('save-result').textContent,/Verbindungsdaten/);
});

test('paused weather reconciliation reports that settings were saved',async()=>{
  const ui=await setup(queued,success,{existing:true,savePaused:true});
  await ui.form.onsubmit({preventDefault(){}});
  assert.match(ui.get('save-result').textContent,/Einstellungen gespeichert/);
  assert.match(ui.get('save-result').textContent,/pausiert/);
  assert.match(ui.get('save-result').className,/pending/);
});

test('tabs separate overview, settings and categories without losing the draft',async()=>{
  const ui=await setup(queued,success,{existing:true});
  ui.get('tab-category').onclick();
  assert.equal(ui.get('page-category').hidden,false);
  assert.equal(ui.get('connection-editor').hidden,true);
  assert.equal(ui.get('categories-editor').hidden,false);
  vm.runInContext('openCategory(0)',ui.context);
  ui.get('normal-soc').value='45';
  ui.get('apply-category').onclick();
  ui.get('tab-home').onclick();
  assert.equal(ui.get('editor').hidden,true);
  assert.equal(ui.get('page-home').hidden,false);
  ui.get('tab-settings').onclick();
  assert.equal(ui.get('connection-editor').hidden,false);
  ui.get('check-interval').value='10';
  ui.get('tab-category').onclick();
  await ui.form.onsubmit({preventDefault(){}});
  const payload=JSON.parse(ui.calls.find(call=>call.url==='api/saves').options.body);
  assert.equal(payload.categories[0].normal_soc,45);
  assert.equal(payload.check_interval_minutes,10);
  assert.equal(ui.get('category-device').value,'existing');
});

test('changing interval alone does not invalidate the existing endpoint',async()=>{
  const ui=await setup(queued,success,{existing:true});
  ui.get('check-interval').value='15';
  await ui.form.onsubmit({preventDefault(){}});
  const payload=JSON.parse(ui.calls.find(call=>call.url==='api/saves').options.body);
  assert.equal(payload.check_interval_minutes,15);
  assert.equal(ui.calls.filter(call=>call.url==='api/probes').length,0);
  assert.equal(ui.get('check-interval').value,'15');
});

test('invalid interval prevents saving and returns to settings',async()=>{
  const ui=await setup(queued,success,{existing:true});
  ui.get('tab-category').onclick();
  ui.get('check-interval').value='1.5';
  await ui.form.onsubmit({preventDefault(){}});
  assert.equal(ui.calls.filter(call=>call.url==='api/saves').length,0);
  assert.equal(ui.get('page-settings').hidden,false);
  assert.match(ui.get('notice').textContent,/Abfrageintervall/);
});

test('new inverter categories can be edited in their own tab before first save',async()=>{
  const ui=await setup(queued,success);
  ui.get('tab-category').onclick();
  assert.equal(ui.get('category-device').value,'__new');
  assert.equal(ui.get('categories-editor').hidden,false);
  assert.equal(ui.get('save-device').disabled,true);
  ui.get('tab-settings').onclick();
  await ui.get('test-device').onclick();
  ui.get('tab-category').onclick();
  assert.equal(ui.get('save-device').disabled,false);
});
