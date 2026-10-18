function escapeHtml(value="") {const e=document.createElement('span');e.textContent=String(value);return e.innerHTML;}
async function api(path,options={}) {const r=await fetch(path,{...options,headers:options.body instanceof FormData?{}:{'Content-Type':'application/json'}});const d=await r.json();if(!r.ok)throw Error(typeof d.detail==='string'?d.detail:JSON.stringify(d.detail));return d;}
