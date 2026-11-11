const $ = (id) => document.getElementById(id);
const KEY_NAME = "nova-fetch-api-key";
let configState = null, pollHandle = null, toastHandle = null;
const key = () => localStorage.getItem(KEY_NAME) || "";
const esc = (value) => String(value ?? "").replace(/[&<>"']/g, ch => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[ch]));
const bytes = (value) => { let n=Number(value||0); for (const unit of ["B","KB","MB","GB"]) { if(n<1024||unit==="GB") return n.toFixed(1)+" "+unit; n/=1024; } };
function toast(message) { const el=$("toast"); el.textContent=message; el.classList.add("show"); clearTimeout(toastHandle); toastHandle=setTimeout(()=>el.classList.remove("show"),3200); }
function message(text, good=false) { $("form-message").textContent=text||""; $("form-message").classList.toggle("good",good); }
async function api(path, options={}) {
  const headers = new Headers(options.headers || {});
  if(key()) headers.set("X-API-Key",key());
  if(options.body && !headers.has("Content-Type")) headers.set("Content-Type","application/json");
  const res=await fetch(path,{...options,headers});
  const payload=(res.headers.get("content-type")||"").includes("application/json") ? await res.json() : null;
  if(!res.ok) { if(res.status===401) openSettings(); throw new Error(payload?.detail || "Request failed ("+res.status+")"); }
  return payload;
}
function openSettings() { $("api-key-input").value=key(); $("settings-modal").classList.remove("hidden"); }
function closeSettings() { $("settings-modal").classList.add("hidden"); }
function displayTools(tools) {
  const rows=[
    ["YT-DLP",tools.yt_dlp,tools.yt_dlp!=="missing"],
    ["FFMPEG",tools.ffmpeg?"READY":"MISSING",tools.ffmpeg],
    ["FFPROBE",tools.ffprobe?"READY":"MISSING",tools.ffprobe],
    ["JS RUNTIME",tools.javascript_runtime?"READY":"MISSING",tools.javascript_runtime],
    ["NOVA NATIVE",tools.native_engine||"BUILT-IN",true],
    ["EXTERNAL API",tools.external_downloader_api?"ENABLED":"DISABLED",!tools.external_downloader_api]
  ];
  $("engine-status").innerHTML=rows.map(([name,value,active])=>'<span class="tool '+(active?"":"off")+'"><i></i>'+esc(name)+" · "+esc(value)+"</span>").join("");
}
async function loadConfig() {
  try {
    configState=await api("/api/config"); displayTools(configState.tools);
    if(configState.requires_api_key&&!key()) openSettings();
  } catch(err) { $("engine-status").textContent=err.message; if(err.message.includes("API key"))openSettings(); }
}
function safeImage(url) { try { const u=new URL(url); return ["https:","http:"].includes(u.protocol)?u.href:""; } catch{return "";} }
async function inspect() {
  const url=$("source-urls").value.split(/\r?\n/).map(x=>x.trim()).find(Boolean);
  if(!url)return message("Paste a source URL first.");
  const btn=$("preview-button"); btn.disabled=true; btn.textContent="Inspecting…"; message("");
  try {
    const data=await api("/api/preview?url="+encodeURIComponent(url));
    $("preview-title").textContent=data.title||"Untitled media";
    $("preview-meta").textContent=[data.creator||"Unknown creator",data.duration_label||"Length unknown",data.source||"Source"].join(" · ");
    $("preview-formats").textContent=data.formats?.length?"Formats: "+data.formats.map(x=>x.height+"p "+x.ext).join(" · "):"The engine will choose an available format.";
    const src=safeImage(data.thumbnail); $("preview-image").src=src; $("preview-image").classList.toggle("hidden",!src);
    $("preview-card").classList.remove("hidden"); message("Source inspected. Choose an output and add it to the queue.",true);
  } catch(err) { $("preview-card").classList.add("hidden"); message(err.message); }
  finally { btn.disabled=false; btn.textContent="⌕ Inspect source"; }
}
async function addJobs() {
  const urls=$("source-urls").value.split(/\r?\n/).map(x=>x.trim()).filter(Boolean);
  if(!urls.length)return message("Paste at least one source URL.");
  if(urls.length>10)return message("Add no more than 10 links per batch.");
  if(!$("rights-confirm").checked)return message("Confirm you own the media or have permission to save it.");
  const body={
    mode:$("mode").value,quality:$("quality").value,container:$("container").value,
    provider:$("provider").value,playlist:$("playlist").checked,subtitles:$("subtitles").checked,
    subtitle_lang:"en",embed_metadata:$("metadata").checked,embed_thumbnail:$("thumbnail").checked,
    rights_confirmed:true
  };
  const btn=$("queue-button"); btn.disabled=true; btn.textContent="ADDING JOBS…"; message("");
  let count=0;
  try {
    for(const url of urls) { await api("/api/downloads",{method:"POST",body:JSON.stringify({...body,url})}); count++; }
    message(count+" job"+(count===1?"":"s")+" added to the queue.",true);
    toast(count+" download job"+(count===1?"":"s")+" queued"); await refreshQueue();
  } catch(err) { message(count+" added. "+err.message); }
  finally { btn.disabled=false; btn.innerHTML="＋ Add to download queue <b>↗</b>"; }
}
function renderQueue(jobs) {
  const active=jobs.filter(j=>["queued","downloading","processing"].includes(j.status)).length;
  const done=jobs.filter(j=>j.status==="completed").length;
  const failed=jobs.filter(j=>["failed","interrupted"].includes(j.status)).length;
  $("metric-active").textContent=active; $("metric-done").textContent=done; $("metric-failed").textContent=failed;
  if(!jobs.length) {
    $("queue-list").innerHTML='<div class="empty"><div>↓</div><b>Your queue is clear</b><span>Add a link above to start your first job.</span></div>'; return;
  }
  $("queue-list").innerHTML=jobs.map(job=>{
    const status=job.status||"queued", pct=Math.max(0,Math.min(100,Number(job.percent||0)));
    const title=job.title&&job.title!=="Queued media"?job.title:"Media download";
    const detail=[String(job.mode||"").toUpperCase(),job.quality==="best"?"BEST":job.quality+"P",String(job.provider||"").toUpperCase(),job.stage].filter(Boolean).join(" · ");
    let actions="";
    if(status==="completed") actions='<button class="save-file" data-action="download" data-id="'+esc(job.id)+'" data-name="'+esc(job.output_name||"media-file")+'">SAVE FILE ↓</button><button data-action="remove" data-id="'+esc(job.id)+'">REMOVE</button>';
    else if(["queued","downloading","processing"].includes(status)) actions='<button data-action="cancel" data-id="'+esc(job.id)+'">CANCEL</button>';
    else actions='<button data-action="remove" data-id="'+esc(job.id)+'">REMOVE</button>';
    return '<article class="job"><div class="job-main"><div class="job-title">'+esc(title)+'</div><div class="job-meta"><b class="status status-'+esc(status)+'">'+esc(status)+'</b><span>'+esc(detail)+'</span><span>#'+esc(job.id)+'</span></div><div class="progress"><i style="width:'+pct+'%"></i></div><div class="job-stats"><span>'+pct.toFixed(1)+'% · '+esc(job.speed||"—")+'</span><span>'+esc(job.eta||"—")+' · '+(job.output_bytes?bytes(job.output_bytes):"")+'</span></div>'+(job.error?'<div class="job-error">'+esc(job.error)+'</div>':"")+'</div><div class="job-actions">'+actions+'</div></article>';
  }).join("");
}
async function refreshQueue() {
  try { renderQueue(await api("/api/downloads")); }
  catch(err) { if(err.message.includes("API key")) clearInterval(pollHandle); }
}
async function saveFile(id,name) {
  const headers=new Headers(); if(key())headers.set("X-API-Key",key());
  const res=await fetch("/api/downloads/"+encodeURIComponent(id)+"/file",{headers});
  if(!res.ok){const data=await res.json().catch(()=>({})); if(res.status===401)openSettings(); throw new Error(data.detail||"Could not retrieve file.");}
  const blob=await res.blob(), href=URL.createObjectURL(blob), a=document.createElement("a");
  a.href=href; a.download=name||"nova-fetch-download"; document.body.appendChild(a); a.click(); a.remove(); setTimeout(()=>URL.revokeObjectURL(href),30000);
}
async function queueAction(event) {
  const btn=event.target.closest("button[data-action]"); if(!btn)return;
  const {action,id,name}=btn.dataset; btn.disabled=true;
  try {
    if(action==="download") await saveFile(id,name);
    if(action==="cancel") await api("/api/downloads/"+encodeURIComponent(id)+"/cancel",{method:"POST"});
    if(action==="remove") await api("/api/downloads/"+encodeURIComponent(id),{method:"DELETE"});
    await refreshQueue();
  } catch(err) { toast(err.message); }
  finally { btn.disabled=false; }
}
function updateMode() {
  const audio=$("mode").value==="audio";
  $("quality-control").classList.toggle("disabled",audio); $("quality").disabled=audio;
  $("container-control").classList.toggle("disabled",audio); $("container").disabled=audio;
}
async function paste() {
  try { $("source-urls").value=await navigator.clipboard.readText(); toast("Link pasted."); }
  catch { toast("Clipboard access is not available. Paste the link manually."); }
}
function startPolling() { clearInterval(pollHandle); pollHandle=setInterval(refreshQueue,1600); }
document.addEventListener("DOMContentLoaded",async()=>{
  $("settings-open").addEventListener("click",openSettings);
  $("settings-close").addEventListener("click",closeSettings);
  $("settings-modal").addEventListener("click",e=>{if(e.target===$("settings-modal"))closeSettings();});
  $("save-key").addEventListener("click",async()=>{const v=$("api-key-input").value.trim(); if(v)localStorage.setItem(KEY_NAME,v);else localStorage.removeItem(KEY_NAME); closeSettings(); await loadConfig(); await refreshQueue(); startPolling();});
  $("clear-key").addEventListener("click",()=>{localStorage.removeItem(KEY_NAME);$("api-key-input").value="";toast("Saved key cleared from this browser.");});
  $("preview-button").addEventListener("click",inspect); $("queue-button").addEventListener("click",addJobs);
  $("queue-list").addEventListener("click",queueAction); $("mode").addEventListener("change",updateMode);
  $("paste-button").addEventListener("click",paste);
  updateMode(); await loadConfig(); await refreshQueue(); startPolling();
});
