from __future__ import annotations

import json
from typing import Any


PLAYGROUND_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Yomai Playground</title>
<style>
:root{color-scheme:dark}body{margin:0;background:#0b0f17;color:#e5e7eb;font-family:Inter,system-ui,sans-serif;display:grid;grid-template-columns:280px 1fr 380px;height:100vh}aside,section{border-right:1px solid #1f2937}.side,.log{padding:16px}.brand{font-weight:800;font-size:20px;margin-bottom:16px}.muted{color:#9ca3af;font-size:12px}select,input,button,textarea{background:#111827;color:#e5e7eb;border:1px solid #374151;border-radius:8px;padding:10px;box-sizing:border-box}button{cursor:pointer}button:hover{background:#1f2937}.chat{display:flex;flex-direction:column;height:100vh}.messages{flex:1;overflow:auto;padding:24px}.bubble{max-width:760px;margin:0 0 14px;padding:14px 16px;border-radius:14px;line-height:1.5;white-space:pre-wrap}.user{background:#1d4ed8;margin-left:auto}.assistant{background:#111827;border:1px solid #1f2937}.composer{display:flex;gap:8px;padding:16px;border-top:1px solid #1f2937}.composer textarea{flex:1;resize:none;height:46px}.panel{overflow:auto}.event{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px;background:#030712;border:1px solid #1f2937;border-radius:8px;padding:8px;margin:8px 0;white-space:pre-wrap}.tool{background:#172554;border:1px solid #2563eb;border-radius:8px;padding:8px;margin:8px 0}.usage{position:fixed;bottom:0;right:0;width:348px;padding:12px;background:#030712;border-top:1px solid #1f2937}.row{display:flex;gap:8px;margin:8px 0}.row>*{flex:1}.field{margin:10px 0}.field label{display:flex;justify-content:space-between;font-size:12px;color:#9ca3af;margin-bottom:4px}.field input{width:100%}.pill{font-size:11px;color:#93c5fd}</style>
</head>
<body>
<aside class="side"><div class="brand">Yomai</div><div class="muted">Route</div><select id="route"></select><div id="routeMeta" class="event"></div><div id="fields"></div><div class="muted" style="margin-top:16px">Session</div><div id="sid" class="event"></div><div class="row"><button onclick="newSession()">New Session</button><button onclick="clearChat()">Clear</button></div></aside>
<main class="chat"><div id="messages" class="messages"></div><div class="composer"><textarea id="input" placeholder="Send a message..."></textarea><button onclick="sendMessage()">Send</button></div></main>
<section class="panel"><div class="log"><h3>Tools</h3><div id="tools"></div><h3>Events</h3><div id="events"></div></div><div class="usage" id="usage">No usage yet</div></section>
<script>
const ROUTES = __ROUTES__;
let sessionId = crypto.randomUUID();
let currentAssistant = null;
const $ = id => document.getElementById(id);
function selectedRoute(){ return ROUTES.find(r=>r.path===$('route').value) || ROUTES[0]; }
function init(){ ROUTES.forEach(r=>{ const o=document.createElement('option'); o.value=r.path; o.textContent=`${r.type}: ${r.path}`; $('route').appendChild(o); }); $('sid').textContent=sessionId; $('route').onchange=()=>{newSession();clearChat();renderRouteFields();}; $('input').addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();sendMessage();}}); renderRouteFields(); }
function renderRouteFields(){ const r=selectedRoute(); if(!r)return; $('routeMeta').textContent=JSON.stringify({type:r.type,tools:r.tools||[],body_params:r.body_params||[]},null,2); const fields=$('fields'); fields.innerHTML=''; if(r.type==='workflow'){ (r.params||[]).forEach((p,idx)=>{ const wrap=document.createElement('div'); wrap.className='field'; const label=document.createElement('label'); label.innerHTML=`<span>${p.name}</span><span class="pill">${p.type}${p.required?' · required':''}</span>`; const input=document.createElement('input'); input.id='field-'+p.name; input.placeholder=idx===0?'Uses chat box if empty':(p.default??''); wrap.appendChild(label); wrap.appendChild(input); fields.appendChild(wrap); }); } }
function newSession(){ sessionId=crypto.randomUUID(); $('sid').textContent=sessionId; }
function clearChat(){ $('messages').innerHTML=''; $('tools').innerHTML=''; $('events').innerHTML=''; $('usage').textContent='No usage yet'; currentAssistant=null; }
function bubble(text, cls){ const d=document.createElement('div'); d.className=`bubble ${cls}`; d.textContent=text; $('messages').appendChild(d); $('messages').scrollTop=$('messages').scrollHeight; return d; }
function logEvent(ev,data){ const d=document.createElement('div'); d.className='event'; d.textContent=ev+' '+JSON.stringify(data,null,2); $('events').prepend(d); }
function handle(ev,data){ logEvent(ev,data); if(ev==='chunk'){ if(!currentAssistant) currentAssistant=bubble('', 'assistant'); currentAssistant.textContent += data.content; } else if(ev==='result'){ bubble(data.content,'assistant'); } else if(ev==='step_start'){ bubble(`▶ step ${data.index}: ${data.name}`,'assistant'); } else if(ev==='step_done'){ bubble(`✓ ${data.name} (${data.duration_ms}ms)`,'assistant'); } else if(ev==='tool_start'){ const d=document.createElement('div'); d.className='tool'; d.id='tool-'+data.id; d.textContent=`${data.name} ${JSON.stringify(data.args)}`; $('tools').prepend(d); } else if(ev==='tool_end'){ const d=$('tool-'+data.id); if(d) d.textContent += ` → ${data.result} (${data.duration_ms}ms)`; } else if(ev==='usage'){ $('usage').textContent=`${data.input_tokens}→${data.output_tokens} tokens · ~$${Number(data.cost_usd).toFixed(6)}`; } else if(ev==='error'){ bubble('Error: '+data.message,'assistant'); } else if(ev==='done'){ currentAssistant=null; } }
function parse(buf){ const parts=buf.split('\n\n'); for(let i=0;i<parts.length-1;i++){ let ev='message', data='{}'; for(const line of parts[i].split('\n')){ if(line.startsWith('event:')) ev=line.slice(6).trim(); if(line.startsWith('data:')) data=line.slice(5).trim(); } if(ev!=='ping') handle(ev, JSON.parse(data||'{}')); } return parts[parts.length-1]; }
function requestBody(route,msg){ if(route.type!=='workflow') return {message:msg}; const body={}; (route.params||[]).forEach((p,idx)=>{ const el=$('field-'+p.name); const value=el?el.value.trim():''; if(value) body[p.name]=value; else if(idx===0) body[p.name]=msg; else if(p.default!==undefined && p.default!==null) body[p.name]=p.default; }); return body; }
async function sendMessage(){ const msg=$('input').value.trim(); if(!msg)return; $('input').value=''; bubble(msg,'user'); currentAssistant=null; const route=selectedRoute(); const body=requestBody(route,msg); const res=await fetch(route.path,{method:'POST',headers:{'Content-Type':'application/json','X-Session-Id':sessionId},body:JSON.stringify(body)}); if(!res.ok){ bubble(`HTTP ${res.status}: ${await res.text()}`,'assistant'); return; } const reader=res.body.getReader(); const dec=new TextDecoder(); let buf=''; while(true){ const {done,value}=await reader.read(); if(done)break; buf=parse(buf+dec.decode(value,{stream:true})); } }
init();
</script>
</body>
</html>"""


def get_playground_html(routes: list[dict[str, Any]]) -> str:
    return PLAYGROUND_HTML.replace("__ROUTES__", json.dumps(routes))
