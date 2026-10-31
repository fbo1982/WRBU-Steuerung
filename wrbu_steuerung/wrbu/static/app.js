"use strict";
const $ = (id) => document.getElementById(id);
const months = ["Januar","Februar","März","April","Mai","Juni","Juli","August","September","Oktober","November","Dezember"];
let state = null, draft = null, editingCategory = null, proof = null, checking = false;
let proofTimer = null;
const form = $("device-form");
function element(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}
function notice(message="") { $("notice").textContent=message; $("notice").hidden=!message; }
async function api(path, options={}) {
  const response = await fetch(path, {...options, headers:{"Content-Type":"application/json","X-WRBU-Request":"1", ...(options.headers||{})}});
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "Anfrage fehlgeschlagen.");
  return data;
}
function connection() {
  return Object.fromEntries(["name","profile","host","port","unit"].map(key => [
    key, ["port","unit"].includes(key) ? Number(form.elements[key].value) : form.elements[key].value
  ]));
}
function invalidateProof() {
  proof=null; clearTimeout(proofTimer); $("save-device").disabled=true;
  $("test-result").hidden=true;
}
function buttons() {
  $("save-device").disabled = !proof || checking || !draft?.categories.length || !$("category-editor").hidden;
}
function openEditor(device=null) {
  notice(); invalidateProof();
  draft=device?structuredClone(device):{id:null,connection:{name:"",profile:"sungrow_sh",host:"",port:502,unit:1},categories:structuredClone(state.default_categories)};
  $("editor-title").textContent=device?"Wechselrichter bearbeiten":"Wechselrichter anlegen";
  form.elements.profile.replaceChildren(...Object.entries(state.profiles).map(([value,label])=>{
    const option=element("option",label);option.value=value;return option;
  }));
  for (const [key,value] of Object.entries(draft.connection)) if(form.elements[key]) form.elements[key].value=value;
  $("editor").hidden=false; $("category-editor").hidden=true;
  renderCategories(); $("editor").scrollIntoView({behavior:"smooth",block:"start"});
}
function renderDevices() {
  const list=$("devices"); list.replaceChildren();
  if (!state.devices.length) {list.append(element("div","Noch kein Wechselrichter angelegt. Lege deine erste Anlage an und prüfe die Kommunikation.","empty"));return;}
  for (const device of state.devices) {
    const runtime=device.runtime, card=element("article",undefined,"card");
    card.append(element("span",runtime.status||"Warte auf DWD","eyebrow"),element("h2",device.connection.name),
      element("div",device.model+" · "+device.connection.host+":"+device.connection.port,"muted"));
    const soc=element("div",runtime.current===null||runtime.current===undefined?"–":String(runtime.current),"soc");
    soc.append(element("span"," % Backup-SOC"));card.append(soc);
    const dl=element("dl");
    for(const [name,value] of [["Sollwert",runtime.desired===null||runtime.desired===undefined?"–":runtime.desired+" %"],["Kategorie",runtime.category||"–"],["DWD-Stufe",runtime.warning_level??"unbekannt"],["Seriennummer",device.serial]]) {
      const block=element("div"); block.append(element("dt",name),element("dd",String(value)));dl.append(block);
    }
    card.append(dl);
    if(runtime.last_verified) card.append(element("p","Zuletzt vom WR bestätigt: "+new Date(runtime.last_verified).toLocaleString("de-DE"),"muted"));
    if(runtime.error) card.append(element("p",runtime.error,"error"));
    const test=device.test_result;
    card.append(element("p","Einrichtungstest: "+test.before+" % gelesen → "+test.after+" % bestätigt","muted"));
    const actions=element("div",undefined,"actions");
    const edit=element("button","Bearbeiten","secondary small");edit.onclick=()=>openEditor(device);
    const remove=element("button","Löschen","danger small");remove.onclick=async()=>{
      if(!confirm("Wechselrichter „"+device.connection.name+"“ und seine Regeln löschen? Der WR-Wert bleibt erhalten."))return;
      try{await api("api/devices/"+encodeURIComponent(device.id),{method:"DELETE"});await refresh();}catch(error){notice(error.message);}
    };actions.append(edit,remove);card.append(actions);list.append(card);
  }
}
function renderCategories() {
  const list=$("categories");list.replaceChildren();
  draft.categories.forEach((category,index)=>{
    const row=element("div",undefined,"category-row"), details=element("div");
    details.append(element("strong",category.name),element("p",category.months.map(m=>months[m-1]).join(", ")+" · Normal "+category.normal_soc+" % · DWD "+category.warning_soc+" %"));
    const actions=element("div",undefined,"actions");
    const edit=element("button","Bearbeiten","secondary small");edit.type="button";edit.onclick=()=>openCategory(index);
    const remove=element("button","Löschen","danger small");remove.type="button";remove.onclick=()=>{
      draft.categories.splice(index,1);$("category-editor").hidden=true;renderCategories();
    };actions.append(edit,remove);row.append(details,actions);list.append(row);
  });
  const used=new Set(draft.categories.flatMap(c=>c.months)),free=months.filter((_,i)=>!used.has(i+1));
  $("free-months").textContent=free.length?"Ohne Regel: "+free.join(", ")+". In diesen Monaten wird auch bei DWD-Warnungen kein SOC gesetzt.":"Alle zwölf Monate sind zugeordnet.";
  $("add-category").disabled=used.size===12;
  buttons();
}
function openCategory(index=null) {
  editingCategory=index;
  const category=index===null?{name:"",months:[],normal_soc:25,warning_soc:60}:draft.categories[index];
  $("category-name").value=category.name;$("normal-soc").value=category.normal_soc;$("warning-soc").value=category.warning_soc;
  const used=new Set(draft.categories.flatMap((c,i)=>i===index?[]:c.months));
  const list=$("months");list.replaceChildren();
  months.forEach((name,i)=>{const label=element("label"),input=element("input");input.type="checkbox";input.value=String(i+1);input.checked=category.months.includes(i+1);input.disabled=used.has(i+1);label.append(input,document.createTextNode(name));list.append(label);});
  $("category-editor").hidden=false;buttons();
}
$("apply-category").onclick=()=>{
  const category={name:$("category-name").value.trim(),months:[...$("months").querySelectorAll("input:checked")].map(input=>Number(input.value)),
    normal_soc:Number($("normal-soc").value),warning_soc:Number($("warning-soc").value)};
  if(!category.name||!category.months.length) {notice("Bitte Kategorienamen und mindestens einen Monat auswählen.");return;}
  if(!Number.isInteger(category.normal_soc)||!Number.isInteger(category.warning_soc)||category.normal_soc<0||category.warning_soc>100||category.warning_soc<category.normal_soc) {notice("Bitte ganze SOC-Werte von 0 bis 100 eingeben. Die Warnreserve darf nicht niedriger sein.");return;}
  if(draft.categories.some((c,i)=>i!==editingCategory&&c.name.toLocaleLowerCase()===category.name.toLocaleLowerCase())) {notice("Dieser Kategoriename ist bereits vergeben.");return;}
  if(editingCategory===null)draft.categories.push(category);else draft.categories[editingCategory]=category;
  $("category-editor").hidden=true;notice();renderCategories();
};
$("cancel-category").onclick=()=>{$("category-editor").hidden=true;buttons();};
$("add-category").onclick=()=>openCategory();
$("add-device").onclick=()=>state&&openEditor();
$("close-editor").onclick=()=>{$("editor").hidden=true;draft=null;invalidateProof();notice();};
for(const key of ["name","profile","host","port","unit"])form.elements[key].addEventListener("input",invalidateProof);
$("test-device").onclick=async()=>{
  if(!["name","profile","host","port","unit"].every(key=>form.elements[key].reportValidity()))return;
  checking=true;invalidateProof();$("test-device").disabled=true;$("test-device").textContent="Kommunikation wird geprüft …";notice();
  const submitted=JSON.stringify(connection()), id=draft.id;
  try {
    const data=await api("api/probe",{method:"POST",body:JSON.stringify({id,connection:JSON.parse(submitted)})});
    if(!draft||draft.id!==id||JSON.stringify(connection())!==submitted)throw new Error("Verbindungsdaten wurden geändert. Bitte erneut prüfen.");
    const r=data.result;
    $("test-result").className="result"+(r.success?"":" failed");$("test-result").hidden=false;
    $("test-result").textContent=(r.success?"Prüfung erfolgreich":"Prüfung fehlgeschlagen")+"\nModell: "+(r.model||"–")+" · Seriennummer: "+(r.serial||"–")+"\nGelesen: "+(r.before??"–")+" % · Schreiben versucht: "+(r.write_attempted?"ja":"nein")+" · Schreibbestätigung: "+(r.write_acknowledged?"ja":"nein")+"\nZurückgelesen: "+(r.after??"–")+" %"+(r.error?"\n"+r.error:"");
    proof=data.proof;
    if(proof)proofTimer=setTimeout(()=>{proof=null;buttons();notice("Die Kommunikationsprüfung ist abgelaufen. Bitte erneut prüfen.");},600000);
  } catch(error){notice(error.message);}
  finally{checking=false;$("test-device").disabled=false;$("test-device").textContent="Kommunikation prüfen";buttons();}
};
form.onsubmit=async event=>{
  event.preventDefault();if(!proof||checking||!$("category-editor").hidden||!form.reportValidity())return;
  $("save-device").disabled=true;notice();
  try{await api("api/devices",{method:"POST",body:JSON.stringify({id:draft.id,connection:connection(),categories:draft.categories,proof})});$("editor").hidden=true;draft=null;invalidateProof();await refresh();}
  catch(error){notice(error.message);buttons();}
};
function renderWeather(){
  const weather=state.weather;
  if(!weather)return;
  const config=weather.config;
  $("weather-summary").textContent=config
    ?config.label+" · "+(weather.ready?"DWD-Stufe "+weather.level:"Warnlage unbekannt")+" · "+(weather.last_update?"Letzte Abfrage: "+new Date(weather.last_update).toLocaleString("de-DE"):"Warte auf erste DWD-Abfrage")
    :"Noch kein Wetterstandort gespeichert. WR-Kommunikation kann unabhängig davon geprüft werden.";
  if(document.activeElement!==$("weather-address")&&!$("weather-address").value&&config)$("weather-address").value=config.address;
  if(weather.error)$("weather-summary").textContent+=" · "+weather.error;
  $("weather-headlines").replaceChildren(...weather.headlines.map(text=>element("li",text)));
}
$("weather-address").addEventListener("input",()=>{$("weather-choice").hidden=true;$("weather-region").replaceChildren();});
$("weather-form").onsubmit=async event=>{
  event.preventDefault();
  if(!$("weather-address").reportValidity())return;
  const address=$("weather-address").value;
  $("weather-search").disabled=true;$("weather-message").textContent="DWD-Warnregion wird gesucht …";$("weather-choice").hidden=true;
  try{
    const data=await api("api/weather/search",{method:"POST",body:JSON.stringify({address})});
    if($("weather-address").value!==address)throw new Error("Adresse wurde geändert. Bitte erneut prüfen.");
    $("weather-region").replaceChildren(...data.candidates.map(candidate=>{
      const option=element("option",candidate.label);option.value=candidate.token;return option;
    }));
    $("weather-choice").hidden=false;
    $("weather-message").textContent="Bitte die passende Gemeinde und das Bundesland kontrollieren und den Wetterstandort speichern.";
  }catch(error){$("weather-message").textContent=error.message;}
  finally{$("weather-search").disabled=false;}
};
$("weather-save").onclick=async()=>{
  const token=$("weather-region").value;if(!token)return;
  $("weather-save").disabled=true;
  try{
    await api("api/weather/location",{method:"POST",body:JSON.stringify({token})});
    $("weather-choice").hidden=true;$("weather-message").textContent="Wetterstandort gespeichert. DWD-Daten werden direkt geladen.";
    await refresh();
  }catch(error){$("weather-message").textContent=error.message;}
  finally{$("weather-save").disabled=false;}
};
async function refresh(){
  try{
    state=await api("api/status");
    $("version").textContent="V"+state.version;
    $("connection").className="banner"+(state.ha_ready?" ok":"");
    $("connection").textContent=state.ha_ready?"Home Assistant verbunden · Zeitzone "+state.timezone:"Zeitzone "+state.timezone+" · WR-Prüfung und direkte DWD-Abfrage sind unabhängig von Home Assistant.";
    $("add-device").disabled=false;
    renderDevices();
    renderWeather();
  }catch(error){$("connection").className="banner";$("connection").textContent="App nicht erreichbar: "+error.message;}
}
async function poll(){await refresh();setTimeout(poll,5000);}
poll();

