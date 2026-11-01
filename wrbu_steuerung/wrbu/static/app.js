"use strict";
const $ = (id) => document.getElementById(id);
const months = ["Januar","Februar","März","April","Mai","Juni","Juli","August","September","Oktober","November","Dezember"];
let state = null, draft = null, editingCategory = null, proof = null, checking = false;
let proofTimer = null;
let saving = false, lockedControls = [], proofSignature = null;
let editorEpoch = 0, proofReason = "Bitte zuerst die Kommunikation erfolgreich prüfen.";
let currentView = "home", draftBaseline = "", pickerSignature = "";
const form = $("device-form");
function draftSignature() {
  return draft?JSON.stringify({connection:connection(),categories:draft.categories,interval:$("check-interval").value}):"";
}
function mayLeaveDraft() {
  return !draft || (draftSignature()===draftBaseline && $("category-editor").hidden) ||
    confirm("Nicht gespeicherte Änderungen verwerfen?");
}
function syncView() {
  for(const view of ["home","settings","category"]){
    $("page-"+view).hidden=view!==currentView;
    if(view===currentView)$("tab-"+view).setAttribute("aria-current","page");
    else $("tab-"+view).removeAttribute("aria-current");
  }
  $("editor").hidden=!draft||currentView==="home";
  $("connection-editor").hidden=currentView!=="settings";
  $("categories-editor").hidden=currentView!=="category";
  $("editor-caption").textContent=currentView==="category"?"MONATSREGELN":"GERÄTEVERWALTUNG";
  $("editor-title").textContent=currentView==="category"?"Kategorien · "+(connection().name||"Neuer Wechselrichter"):
    draft?.id?"Wechselrichter bearbeiten":"Wechselrichter anlegen";
  renderCategoryPicker();
}
function showView(view) {
  if(saving)return;
  currentView=view;
  if(view==="category"&&!draft&&state?.devices.length)openEditor(state.devices[0],"category");
  else syncView();
}
for(const view of ["home","settings","category"])$("tab-"+view).onclick=()=>showView(view);
$("edit-location").onclick=()=>showView("settings");
function renderCategoryPicker() {
  const devices=state?.devices||[], choices=devices.map(d=>[d.id,d.connection.name]);
  if(draft&&!draft.id)choices.push(["__new",connection().name||"Neuer Wechselrichter (Entwurf)"]);
  const signature=JSON.stringify(choices);
  if(signature!==pickerSignature){
    pickerSignature=signature;
    const placeholder=element("option","Wechselrichter auswählen");placeholder.value="";
    $("category-device").replaceChildren(placeholder,...choices.map(([id,name])=>{const option=element("option",name);option.value=id;return option;}));
  }
  $("category-device").value=draft?(draft.id||"__new"):"";
  $("category-device").disabled=saving||checking||!choices.length;
  $("category-context").textContent=draft?"Kategorien für „"+(connection().name||"Neuer Wechselrichter")+"“. Änderungen übernehmen und anschließend unten speichern.":
    devices.length?"Bitte einen Wechselrichter auswählen.":"Bitte zuerst unter Settings einen Wechselrichter anlegen.";
}
$("category-device").onchange=()=>{
  const id=$("category-device").value;
  if((draft?.id|| (draft?"__new":""))===id)return;
  const device=state.devices.find(d=>d.id===id);
  if(device)openEditor(device,"category");
  renderCategoryPicker();
};
function element(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}
function notice(message="") { $("notice").textContent=message; $("notice").hidden=!message; }
async function api(path, options={}) {
  const controller=new AbortController(), timer=setTimeout(()=>controller.abort(),15000);
  try {
    const response=await fetch(path,{...options,signal:controller.signal,headers:{"Content-Type":"application/json","X-WRBU-Request":"1",...(options.headers||{})}});
    let data;
    try {data=await response.json();}
    catch {throw new Error("Die App hat keine lesbare Antwort geliefert (HTTP "+response.status+"). Bitte das App-Protokoll prüfen.");}
    if(!response.ok)throw new Error(data.error||"Anfrage fehlgeschlagen (HTTP "+response.status+").");
    return data;
  } catch(error) {
    if(error.name==="AbortError")throw new Error("Keine Antwort von der App innerhalb von 15 Sekunden. Eine gestartete Prüfung kann im Hintergrund noch laufen; es wird nicht automatisch erneut geschrieben.");
    throw error;
  } finally {clearTimeout(timer);}
}
function connection() {
  return Object.fromEntries(["name","profile","host","port","unit"].map(key => [
    key, ["port","unit"].includes(key) ? Number(form.elements[key].value) : form.elements[key].value
  ]));
}
function invalidateProof(reason) {
  proof=null; proofSignature=null; clearTimeout(proofTimer);
  proofReason=typeof reason==="string"?reason:"Verbindungsdaten geändert. Bitte erneut prüfen.";
  if(!checking)$("test-result").hidden=true;
  buttons();
}
function unchangedSavedConnection() {
  if(!draft?.id)return false;
  const current=connection();
  return ["profile","host","port","unit"].every(key=>current[key]===draft.connection[key]);
}
function verifiedConnection() {
  return Boolean(proof&&proofSignature===JSON.stringify(connection()));
}
function buttons() {
  const verified=verifiedConnection(), reuse=unchangedSavedConnection();
  let reason;
  if(saving)reason="Einstellungen werden gespeichert und der aktuelle Backup-SOC wird abgeglichen.";
  else if(checking)reason="Die Kommunikationsprüfung läuft. Bitte das Ergebnis abwarten.";
  else if(!verified&&!reuse)reason=proofReason;
  else if(!draft?.categories.length)reason="Bitte mindestens eine Kategorie anlegen.";
  else if(!$("category-editor").hidden)reason="Bitte die Kategorie zuerst übernehmen oder verwerfen.";
  else if(reuse)reason="Gespeicherte WR-Verbindung unverändert. Beim Speichern wird der aktuell gültige SOC am WR geprüft und bei Bedarf korrigiert.";
  else reason="Kommunikation erfolgreich geprüft. Beim Speichern wird der aktuelle SOC-Sollwert abgeglichen.";
  $("save-reason").textContent=reason;
  $("save-device").disabled=saving||checking||(!verified&&!reuse)||!draft?.categories.length||!$("category-editor").hidden;
}
function lockEditor(busy) {
  if(busy){
    lockedControls=[...form.querySelectorAll("input,select,button")].map(node=>[node,node.disabled]);
    for(const [node] of lockedControls)node.disabled=true;
  }else{
    for(const [node,disabled] of lockedControls)node.disabled=disabled;
    lockedControls=[];
  }
  $("close-editor").disabled=busy;
  $("add-device").disabled=busy;
}
function showSave(text, failed=false, pending=false) {
  $("save-result").hidden=false;
  $("save-result").className="result"+(failed?" failed":pending?" pending":"");
  $("save-result").textContent=text;
}
function categoryDraftChanged() {
  showSave("Kategorieänderungen im Entwurf übernommen. Bitte „Speichern und SOC abgleichen“ wählen, um sie dauerhaft zu speichern und den WR zu prüfen.",false,true);
}
function validConnection() {
  if(!["name","profile","host","port","unit"].every(key=>form.elements[key].checkValidity())){
    showView("settings");
    return ["name","profile","host","port","unit"].every(key=>form.elements[key].reportValidity());
  }
  return ["name","profile","host","port","unit"].every(key=>form.elements[key].reportValidity());
}
const probeStages={save_config:"Einstellungen speichern",queued:"Warten auf freie Kommunikation",connect:"Verbindung aufbauen",model:"WR-Modell lesen",serial:"Seriennummer lesen",identity:"Geräteidentität prüfen",read_soc:"Backup-SOC lesen",write_soc:"Denselben SOC einmal schreiben",readback:"SOC zurücklesen und vergleichen",complete:"Abgeschlossen"};
function showProbe(text, failed=false) {
  $("test-result").className="result"+(failed?" failed":"");
  $("test-result").hidden=false;
  $("test-result").textContent=text;
}
function updateConnectionHint() {
  $("connection-hint").textContent=form.elements.profile.value==="sungrow_sh_with_ihm"
    ?"iHomeManager-Weiterleitung: IP des iHomeManagers, einen freigeschalteten Modbus-Port (z. B. 503) und die Geräte-ID des weitergeleiteten WR (z. B. 1) eingeben. Die Kommunikationsprüfung bestätigt den SH-Zugriff."
    :"Direkter SH-Zugriff: IP des Wechselrichters/WiNet verwenden. Wenn der direkte Zugriff bei angeschlossenem iHomeManager gesperrt ist, das Profil „Sungrow SH über iHomeManager“ mit dessen IP und Weiterleitungsport wählen.";
}
function probeTarget(connection) {
  return "Ziel: "+connection.host+":"+connection.port+" · Geräte-ID "+connection.unit;
}
function showProbeProgress(job, target) {
  showProbe("Prüfung läuft · "+Math.floor(job.elapsed||0)+" s\n"+probeTarget(target)+"\nSchritt: "+(probeStages[job.stage]||job.stage)+"\n"+job.message);
}
function showProbeReport(job, target) {
  const r=job.result||{}, success=job.state==="complete"&&r.success&&job.proof;
  const error=r.error||job.message||"Keine erfolgreiche Prüfbestätigung erhalten.";
  showProbe((success?"Prüfung erfolgreich":"Prüfung fehlgeschlagen")+"\n"+probeTarget(target)+
    "\nSchritt: "+(probeStages[job.stage]||job.stage||"unbekannt")+
    (success?"":"\nGrund: "+error)+
    "\nModell: "+(r.model||"–")+" · Seriennummer: "+(r.serial||"–")+
    "\nBackup-SOC gelesen: "+(r.before??"–")+" %"+
    "\nSchreiben versucht: "+(r.write_attempted?"ja":"nein")+" · Schreibbestätigung: "+(r.write_acknowledged?"ja":"nein")+
    "\nZurückgelesen: "+(r.after??"–")+" %"+
    (success?"\nDu kannst den Wechselrichter jetzt speichern.":"\nSpeichern bleibt gesperrt. Bitte den angezeigten Grund prüfen."),!success);
}
function openEditor(device=null, view="settings") {
  if(saving||checking||!mayLeaveDraft())return;
  $("save-result").hidden=true;
  editorEpoch++; notice(); invalidateProof("Bitte zuerst die Kommunikation erfolgreich prüfen."); $("test-result").hidden=true;
  draft=device?structuredClone(device):{id:null,connection:{name:"",profile:"sungrow_sh",host:"",port:502,unit:1},categories:structuredClone(state.default_categories),check_interval_minutes:0};
  $("editor-title").textContent=device?"Wechselrichter bearbeiten":"Wechselrichter anlegen";
  form.elements.profile.replaceChildren(...Object.entries(state.profiles).map(([value,label])=>{
    const option=element("option",label);option.value=value;return option;
  }));
  for (const [key,value] of Object.entries(draft.connection)) if(form.elements[key]) form.elements[key].value=value;
  $("check-interval").value=draft.check_interval_minutes??0;
  draftBaseline=draftSignature();
  currentView=view;
  updateConnectionHint();
  $("editor").hidden=false; $("category-editor").hidden=true;
  renderCategories();syncView(); $("editor").scrollIntoView({behavior:"smooth",block:"start"});
}
function renderDevices() {
  const list=$("devices"); list.replaceChildren();
  if (!state.devices.length) {list.append(element("div","Noch kein Wechselrichter angelegt. Unter Settings die erste Anlage einrichten.","empty"));renderSettingsDevices();renderCategoryPicker();return;}
  for (const device of state.devices) {
    const runtime=device.runtime, card=element("article",undefined,"card");
    card.append(element("span",runtime.status||"Warte auf DWD","eyebrow"),element("h2",device.connection.name),
      element("div",device.model,"muted"));
    const soc=element("div",runtime.current===null||runtime.current===undefined?"–":String(runtime.current),"soc");
    soc.append(element("span"," % Backup-SOC"));card.append(soc);
    const dl=element("dl");
    for(const [name,value] of [["Sollwert",runtime.desired===null||runtime.desired===undefined?"–":runtime.desired+" %"],["Kategorie",runtime.category||"–"],["DWD-Stufe",runtime.warning_level??"unbekannt"],["Abfrageintervall",device.check_interval_minutes?device.check_interval_minutes+" Min.":"Aus"]]) {
      const block=element("div"); block.append(element("dt",name),element("dd",String(value)));dl.append(block);
    }
    card.append(dl);
    if(runtime.last_verified) card.append(element("p","Zuletzt vom WR bestätigt: "+new Date(runtime.last_verified).toLocaleString("de-DE"),"muted"));
    if(runtime.error) card.append(element("p",runtime.error,"error"));
    if(runtime.next_check)card.append(element("p","Nächster Abgleich: "+new Date(runtime.next_check).toLocaleString("de-DE"),"muted"));
    const actions=element("div",undefined,"actions");
    const edit=element("button","Settings","secondary small");edit.onclick=()=>openEditor(device);
    const category=element("button","Kategorie","secondary small");category.onclick=()=>openEditor(device,"category");
    actions.append(edit,category);card.append(actions);list.append(card);
  }
  renderSettingsDevices();renderCategoryPicker();
}
function renderSettingsDevices(){
  const list=$("settings-devices");list.replaceChildren();
  if(!state.devices.length){list.append(element("p","Noch kein Wechselrichter eingerichtet.","hint"));return;}
  for(const device of state.devices){
    const row=element("div",undefined,"category-row"),details=element("div"),actions=element("div",undefined,"actions");
    details.append(element("strong",device.connection.name),element("p",device.model+" · "+device.connection.host+":"+device.connection.port+" · Geräte-ID "+device.connection.unit+" · SN "+device.serial));
    const edit=element("button","Einstellungen","secondary small");edit.disabled=saving;edit.onclick=()=>openEditor(device);
    const remove=element("button","Löschen","danger small");remove.onclick=async()=>{
      if(saving||checking)return;
      if(draft?.id===device.id&&!mayLeaveDraft())return;
      if(!confirm("Wechselrichter „"+device.connection.name+"“ und seine Regeln löschen? Der WR-Wert bleibt erhalten."))return;
      try{await api("api/devices/"+encodeURIComponent(device.id),{method:"DELETE"});if(draft?.id===device.id){draft=null;syncView();}await refresh();}catch(error){notice(error.message);}
    };remove.disabled=saving;actions.append(edit,remove);row.append(details,actions);list.append(row);
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
      if(saving)return;
      draft.categories.splice(index,1);$("category-editor").hidden=true;renderCategories();categoryDraftChanged();
    };actions.append(edit,remove);row.append(details,actions);list.append(row);
  });
  const used=new Set(draft.categories.flatMap(c=>c.months)),free=months.filter((_,i)=>!used.has(i+1));
  $("free-months").textContent=free.length?"Ohne Regel: "+free.join(", ")+". In diesen Monaten wird auch bei DWD-Warnungen kein SOC gesetzt.":"Alle zwölf Monate sind zugeordnet.";
  $("add-category").disabled=used.size===12;
  buttons();
}
function openCategory(index=null) {
  if(saving)return;
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
  $("category-editor").hidden=true;notice();renderCategories();categoryDraftChanged();
};
$("cancel-category").onclick=()=>{$("category-editor").hidden=true;buttons();};
$("add-category").onclick=()=>openCategory();
$("add-device").onclick=()=>state&&openEditor();
$("close-editor").onclick=()=>{if(saving||checking||!mayLeaveDraft())return;editorEpoch++;draft=null;invalidateProof("Bitte zuerst die Kommunikation erfolgreich prüfen.");$("test-result").hidden=true;notice();syncView();};
for(const key of ["name","profile","host","port","unit"])form.elements[key].addEventListener("input",invalidateProof);
form.elements.profile.addEventListener("change",updateConnectionHint);
$("test-device").onclick=async()=>{
  if(!validConnection()||checking||saving)return;
  const epoch=editorEpoch, target=connection(), submitted=JSON.stringify(target), id=draft.id;
  checking=true;invalidateProof("Prüfung läuft.");$("test-device").disabled=true;$("test-device").textContent="Prüfung läuft …";notice();
  showProbe("Prüfung wird gestartet …\n"+probeTarget(target)+"\nEs wird kein Wert verändert: Der gelesene Backup-SOC wird einmal identisch zurückgeschrieben.");
  $("test-result").scrollIntoView({behavior:"smooth",block:"nearest"});
  try {
    let job=await api("api/probes",{method:"POST",body:JSON.stringify({id,connection:target})});
    const started=Date.now();
    while(job.state==="queued"||job.state==="running"){
      if(epoch!==editorEpoch||!draft)return;
      showProbeProgress(job,target);
      if(Date.now()-started>90000)throw new Error("Die Prüfung läuft länger als erwartet. Sie kann im Hintergrund noch laufen; ein erneuter Klick verbindet sich mit dem laufenden Auftrag.");
      await new Promise(resolve=>setTimeout(resolve,700));
      job=await api("api/probes/"+encodeURIComponent(job.id));
    }
    if(epoch!==editorEpoch||!draft)return;
    if(draft.id!==id||JSON.stringify(connection())!==submitted)throw new Error("Verbindungsdaten während der Prüfung geändert. Dieses Ergebnis erlaubt kein Speichern; bitte die neuen Daten prüfen.");
    showProbeReport(job,target);
    if(job.state==="complete"&&job.result?.success&&job.proof){
      proof=job.proof;proofSignature=submitted;proofReason="Prüfung erfolgreich.";
      proofTimer=setTimeout(()=>{proof=null;proofReason="Prüfung abgelaufen. Bitte erneut prüfen.";buttons();$("test-result").textContent+="\nDie Prüfbestätigung ist inzwischen abgelaufen.";},600000);
    } else {
      proof=null;proofReason="Prüfung fehlgeschlagen. Den Grund findest du direkt unter „Kommunikation prüfen“.";
    }
    $("test-result").scrollIntoView({behavior:"smooth",block:"nearest"});
  } catch(error){
    if(epoch===editorEpoch&&draft){
      proof=null;proofReason="Keine erfolgreiche Prüfbestätigung. Bitte die Meldung unter „Kommunikation prüfen“ beachten.";
      showProbe("Prüfung nicht erfolgreich abgeschlossen\n"+probeTarget(target)+"\n"+error.message+"\nSpeichern bleibt gesperrt.",true);
      $("test-result").scrollIntoView({behavior:"smooth",block:"nearest"});
    }
  } finally {
    checking=false;$("test-device").disabled=false;$("test-device").textContent="Kommunikation prüfen";buttons();
  }
};
form.onsubmit=async event=>{
  event.preventDefault();
  if(saving||checking||!$("category-editor").hidden||!validConnection())return;
  if(!verifiedConnection()&&!unchangedSavedConnection()){
    showSave("Neue oder geänderte Verbindungsdaten bitte zuerst erfolgreich prüfen.",true);buttons();return;
  }
  const interval=Number($("check-interval").value);
  if(!Number.isInteger(interval)||interval<0||interval>1440){notice("Abfrageintervall: 0 (Aus) oder 1 bis 1440 ganze Minuten eingeben.");showView("settings");return;}
  const payload={id:draft.id,connection:connection(),categories:structuredClone(draft.categories),check_interval_minutes:interval,proof:verifiedConnection()?proof:null};
  saving=true;lockEditor(true);buttons();notice();
  $("save-device").textContent="Speichern läuft …";
  showSave("Einstellungen werden gespeichert. Der aktuell gültige SOC wird anhand von Monat und DWD-Lage ermittelt und am WR geprüft.");
  $("save-result").scrollIntoView({behavior:"smooth",block:"nearest"});
  try{
    let job=await api("api/saves",{method:"POST",body:JSON.stringify(payload)});
    const started=Date.now();
    while(job.state==="queued"||job.state==="running"){
      showSave("Speichern und SOC-Abgleich laufen · "+Math.floor(job.elapsed||0)+" s\nSchritt: "+(probeStages[job.stage]||job.stage)+"\n"+job.message);
      if(Date.now()-started>90000)throw new Error("Der Vorgang läuft länger als erwartet und kann im Hintergrund weiterlaufen. Bitte den gespeicherten Stand prüfen.");
      await new Promise(resolve=>setTimeout(resolve,700));
      job=await api("api/saves/"+encodeURIComponent(job.id));
    }
    if(job.state!=="complete"||!job.result?.save_result?.saved)throw new Error(job.error||job.message||"Speichern wurde nicht bestätigt. Bitte den gespeicherten Stand prüfen.");
    const device=job.result, result=device.save_result;
    draft=structuredClone(device);
    $("check-interval").value=device.check_interval_minutes??interval;
    for(const [key,value] of Object.entries(draft.connection))if(form.elements[key])form.elements[key].value=value;
    draftBaseline=draftSignature();
    proof=null;proofSignature=null;clearTimeout(proofTimer);
    proofReason="Gespeicherte WR-Verbindung kann für weitere Kategorieänderungen verwendet werden.";
    const details="\nKategorie: "+(result.category||"–")+" · DWD-Stufe: "+(result.warning_level??"unbekannt")+
      "\nAktuell gültiger Sollwert: "+(result.desired??"–")+" % · Bestätigter Backup-SOC: "+(result.current??"–")+" %"+
      (result.checked_at?"\nStand des Speicherabgleichs: "+new Date(result.checked_at).toLocaleString("de-DE"):"");
    showSave(result.message+details,false,!result.applied);
    await refresh();
  }catch(error){
    showSave("Speichervorgang nicht vollständig bestätigt\n"+error.message+"\nBitte den gespeicherten Stand kontrollieren; die WR-Prüfung kann im Hintergrund weiterlaufen.",true);
  }finally{
    saving=false;lockEditor(false);$("save-device").textContent="Speichern und SOC abgleichen";buttons();syncView();
    $("save-result").scrollIntoView({behavior:"smooth",block:"nearest"});
  }
};
function renderWeather(){
  const weather=state.weather;
  if(!weather)return;
  const config=weather.config;
  $("home-address").textContent=config?.address||"Noch kein Standort eingerichtet. Unter Settings eine Anlagenadresse hinterlegen.";
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
    $("add-device").disabled=saving;
    renderDevices();
    renderWeather();
  }catch(error){$("connection").className="banner";$("connection").textContent="App nicht erreichbar: "+error.message;}
}
async function poll(){await refresh();setTimeout(poll,5000);}
poll();

