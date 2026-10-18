(() => {
  const el=id=>document.getElementById(id), esc=escapeHtml;
  let doc=null, selected=null, edits={}, order=[], bounds={}, catalog={active:{},history:[]}, fonts=[];
  let undo=[], redo=[], busy=false, dirty=false, revision=0, previewToken=0, timer, imageURL, focusState;
  let channel='', template='', layerQuery='', layerKind='';
  let keepRatio=true;
  const base=()=>`/api/template-lab/${encodeURIComponent(channel)}`;
  const path=()=>`${base()}/${doc.id}`;
  const status=message=>el('lab-status').textContent=message;
  function deleteLayer(){
    if(busy||!layer()||values().locked||edits[selected]?.deleted)return;
    if(!confirm(`“${layer().name}” 레이어를 삭제할까요? 실행 취소로 복원할 수 있습니다.`))return;
    remember();
    edits[selected]={...edits[selected],deleted:true,visible:false};
    for(const id of ['lab-text-layer','lab-image-layer','lab-eyebrow-layer','lab-caption-layer','lab-badge-layer']){
      const field=el(id);if(field.value===selected)field.value='';
    }
    select([...order].reverse().find(id=>!edits[id]?.deleted)||null);
    changed();
  }
  el('lab-delete-layer').onclick=deleteLayer;
  document.addEventListener('keydown',event=>{
    if(event.defaultPrevented||event.isComposing||event.key!=='Delete'||event.target.closest('input,textarea,select,[contenteditable]')||document.querySelector('dialog[open]'))return;
    if(!doc)return;event.preventDefault();deleteLayer();
  });
  const snapshot=()=>JSON.stringify({edits,order});
  const layer=()=>doc?.layers.find(l=>l.id===selected);
  const values=()=>({...layer(),...edits[selected]});
  const remember=(state=snapshot())=>{if(undo.at(-1)!==state)undo.push(state);undo=undo.slice(-40);redo=[];};
  function lock(on){busy=on;document.querySelectorAll('button,input,select,textarea').forEach(e=>{if(on){e.dataset.busyDisabled=String(e.disabled);e.disabled=true;}else if('busyDisabled' in e.dataset){e.disabled=e.dataset.busyDisabled==='true';delete e.dataset.busyDisabled;}});}
  function changed(){dirty=true;draftState();clearTimeout(timer);timer=setTimeout(preview,300);status('저장하지 않은 변경');}
  function discard(){return !dirty||confirm('저장하지 않은 변경을 버리고 이동할까요?');}
  function draftState(){
    if(!doc)return;
    const linked=Object.entries(catalog.active).filter(([,a])=>a?.draft_id===doc.id);
    const labels={cover:'표지',body:'본문',cta:'CTA'};
    el('lab-draft-state').textContent=dirty?'편집용 초안 · 저장하지 않은 변경':linked.length?linked.map(([role,a])=>`${labels[role]} ${a.draft_matches_active===true?'적용본과 동일':a.draft_matches_active===false?'적용본과 다름':'연결 초안'}`).join(' · '):'편집용 초안 · 채널 미적용';
  }
  function roleTags(id){const tags=[];for(const role of ['cover','body']){const a=catalog?.active?.[role];if(!a||a.draft_id!==template)continue;const names={title:'제목',body:role==='cover'?'보조 글자':'본문',eyebrow:'배지 글자',page:'쪽 번호'};for(const [f,k] of Object.entries(a.layers||{}))if(k===id)tags.push(names[f]||f);if(a.image_layer===id)tags.push('기사 사진');if((a.accent_layer||a.text_options?.eyebrow?.align_to_layer)===id)tags.push(a.ai_accent?'AI 배지 색':'배지 상자');}return tags.map(t=>`<em class="lab-role-tag${t==='AI 배지 색'?' ai':''}">${t}</em>`).join('');}
  function rows(){el('lab-layers').innerHTML=[...order].reverse().filter(id=>{const item=doc.layers.find(layer=>layer.id===id);return !edits[id]?.deleted&&item.name.toLowerCase().includes(layerQuery)&&(!layerKind||(layerKind==='image'?['pixel','smartobject'].includes(item.kind):layerKind==='shape'?Boolean(item.shape):layerKind==='gradientfill'?Boolean(item.gradient):item.kind===layerKind));}).map(id=>{const l=doc.layers.find(l=>l.id===id),v={...l,...edits[id]};return `<div class="lab-layer-row ${id===selected?'selected':''}" data-layer="${id}" draggable="${!v.locked}"><input type="checkbox" data-visible="${id}" aria-label="${esc(l.name)} 표시" ${v.visible?'checked':''}><button type="button" data-select="${id}" aria-pressed="${id===selected}"><img src="${path()}/layer-image/${id}" alt=""><span>${esc(l.name)}${roleTags(id)}<small>${esc((l.group_path||[]).join(' / '))} · ${v.locked?'잠김':l.text_editable?'글자 편집':l.kind==='smartobject'?'원본 이미지 보존':esc(l.kind)}</small></span></button></div>`;}).join('');}
  const number=(key,label,value,min,max,step=1)=>`<label>${label}<input data-prop="${key}" type="number" value="${value}" min="${min}" max="${max}" step="${step}"></label>`;
  const color=(key,label,value)=>`<fieldset class="pc-color-control"><legend>${label}</legend><label>색상<input data-prop="${key}" type="color" value="${esc(value)}"></label><label>HEX<input data-color-hex type="text" value="${esc(value)}" pattern="#[0-9a-fA-F]{6}" maxlength="7"></label><div class="pc-rgb">${['R','G','B'].map((name,index)=>`<label>${name}<input data-color-rgb="${index}" type="number" min="0" max="255" step="1" value="${parseInt(value.slice(1+index*2,3+index*2),16)}"></label>`).join('')}</div><div class="pc-swatches">${['#000000','#ffffff','#e53935','#ffb300','#43a047','#1e88e5','#8e24aa'].map(swatch=>`<button type="button" data-color-preset="${swatch}" style="background:${swatch}" aria-label="${label} ${swatch} 적용"></button>`).join('')}</div></fieldset>`;
  function resizeDimensions(current,key,value,linked){
    const change={[key]:value};
    if(linked){
      const other=key==='width'?'height':'width';
      change[other]=Math.round(current[other]*value/current[key]);
      if(change[other]<1||change[other]>4096)return null;
    }
    return change;
  }
  function alignLayer(direction){
    const current=values(), box=bounds[selected]||[current.x,current.y,current.width,current.height];
    const horizontal=['left','center','right'].includes(direction), axis=horizontal?0:1;
    const factor={left:0,center:.5,right:1,top:0,middle:.5,bottom:1}[direction];
    const key=horizontal?'x':'y';
    return {[key]:Math.round(current[key]+(doc.size[axis]-box[axis+2])*factor-box[axis])};
  }
  function syncColor(input){
    const group=input.closest('.pc-color-control');
    if(!group)return;
    group.querySelector('[data-color-hex]').value=input.value;
    group.querySelectorAll('[data-color-rgb]').forEach(field=>field.value=parseInt(input.value.slice(1+Number(field.dataset.colorRgb)*2,3+Number(field.dataset.colorRgb)*2),16));
  }
  function editColor(input,preset){
    if(busy||values().locked||(!preset&&!input.checkValidity()))return;
    const group=input.closest('.pc-color-control'), picker=group.querySelector('[type=color]');
    let value=preset||input.value;
    if(input.dataset.colorRgb!==undefined){
      const fields=[...group.querySelectorAll('[data-color-rgb]')];
      if(fields.some(field=>!field.checkValidity()||field.value===''))return;
      value='#'+fields.map(field=>Number(field.value).toString(16).padStart(2,'0')).join('');
    }
    if(!/^#[0-9a-f]{6}$/i.test(value))return;
    remember();picker.value=value;
    picker.dispatchEvent(new Event('input',{bubbles:true}));
  }
  function properties(){
    const l=layer();if(!l||edits[selected]?.deleted){el('lab-properties').innerHTML='<p>레이어를 선택하세요.</p>';return;}const v=values(),e=edits[selected]||{};
    let html=`<h3>${esc(l.name)}</h3><label><input type="checkbox" data-prop="locked" ${v.locked?'checked':''}>레이어 잠금</label><h4>변형</h4><label><input id="lab-keep-ratio" type="checkbox" ${keepRatio?'checked':''}>너비·높이 비율 잠금</label><div class="lab-property-grid">${number('x','X',v.x,-10000,10000)}${number('y','Y',v.y,-10000,10000)}${number('width','너비 px',v.width,1,4096)}${number('height','높이 px',v.height,1,4096)}${number('rotation','회전 °',v.rotation||0,-360,360)}${number('opacity','원본 대비 불투명도',e.opacity??1,0,1,.01)}</div><p>원본 레이어 불투명도 ${Math.round((l.source_opacity??255)/255*100)}%</p><div class="lab-order"><button data-order="1">앞으로</button><button data-order="-1">뒤로</button></div><h4>캔버스 기준 정렬</h4><div class="pc-align">${[['left','왼쪽'],['center','가로 중앙'],['right','오른쪽'],['top','위쪽'],['middle','세로 중앙'],['bottom','아래쪽']].map(([key,label])=>`<button type="button" data-align="${key}">${label}</button>`).join('')}</div><label>혼합 모드<select data-prop="blend_mode"><option value="">원본 · ${esc(l.blend_mode||'NORMAL')}</option>${['NORMAL','MULTIPLY','SCREEN','OVERLAY','DARKEN','LIGHTEN','SOFT_LIGHT'].map(mode=>`<option value="${mode}" ${e.blend_mode===mode?'selected':''}>${mode.replaceAll('_',' ')}</option>`).join('')}</select></label>`;
    if(l.group_path?.length)html+='<label><input id="lab-group-move" type="checkbox">같은 그룹 함께 이동</label>';
    if(l.text_editable){html+=`<label>글자<textarea data-prop="text" maxlength="2000">${esc(v.text.replace(/\r/g,'\n'))}</textarea></label><label>폰트<select data-prop="font_id"><option value="">원본 · ${esc(l.font_name)}</option>${fonts.map(f=>`<option value="${f.id}" ${e.font_id===f.id?'selected':''}>${esc(f.name)}</option>`).join('')}</select></label><div class="lab-property-grid">${number('font_size','글자 크기',v.font_size,1,512,.1)}${number('leading','행간',v.leading,1,1024,.1)}${number('tracking','자간',v.tracking||0,-200,1000)}${color('text_color','글자색',v.text_color||'#ffffff')}</div><label>문단 정렬<select data-prop="align">${['left','center','right'].map((k,i)=>`<option value="${k}" ${v.align===k?'selected':''}>${['왼쪽','가운데','오른쪽'][i]}</option>`).join('')}</select></label>`;}
    if(l.text_editable){
      const fx=v.text_effects||{};
      const shadow=fx.shadow||{color:'#000000',opacity:0,distance:4,angle:90,blur:4};
      const stroke=fx.stroke||{color:'#000000',width:0,opacity:1};
      html+=`<details><summary>글자 효과</summary><h4>그림자</h4><div class="lab-property-grid">${color('text_effects.shadow.color','색상',shadow.color)}${number('text_effects.shadow.opacity','불투명도',shadow.opacity,0,1,.01)}${number('text_effects.shadow.distance','거리 px',shadow.distance,0,200)}${number('text_effects.shadow.angle','각도',shadow.angle,-360,360)}${number('text_effects.shadow.blur','흐림 px',shadow.blur,0,200)}</div><h4>외곽선</h4><div class="lab-property-grid">${color('text_effects.stroke.color','색상',stroke.color)}${number('text_effects.stroke.width','두께 px',stroke.width,0,100,.5)}${number('text_effects.stroke.opacity','불투명도',stroke.opacity,0,1,.01)}</div></details>`;
      if(v.text_runs?.length)html+=`<details><summary>구간별 글자 스타일 (${v.text_runs.length})</summary><p>0부터 시작하는 글자 위치입니다. 끝 위치는 포함하지 않습니다. 전체 글자 크기·색상을 변경했다면 구간별 값보다 우선합니다.</p>${v.text_runs.map((r,i)=>`<div class="lab-property-grid">${number(`text_runs.${i}.start`,'시작',r.start,0,2000)}${number(`text_runs.${i}.end`,'끝',r.end,1,2001)}${number(`text_runs.${i}.font_size`,'크기',r.font_size,1,512,.1)}${color(`text_runs.${i}.text_color`,'색상',r.text_color)}</div>`).join('')}<button type="button" id="lab-use-runs">전체 스타일 덮어쓰기 해제</button></details>`;
    }
    else if(l.kind==='type')html+='<p>원본 이미지로 보존됩니다. 폰트 또는 글자 효과 지원 여부를 확인하세요.</p>';
    if(l.shape){const s=v.shape;html+=`<h4>사각형</h4><div class="lab-property-grid">${color('shape.fill','채우기',s.fill)}${color('shape.stroke','테두리',s.stroke)}${number('shape.stroke_width','테두리 px',s.stroke_width,0,100,.5)}</div><label><input data-prop="shape.fill_enabled" type="checkbox" ${s.fill_enabled?'checked':''}>채우기 표시</label><label><input data-prop="shape.stroke_enabled" type="checkbox" ${s.stroke_enabled?'checked':''}>테두리 표시</label>`;}
    if(l.gradient){const g=v.gradient;html+=`<h4>선형 그라데이션</h4><div class="lab-property-grid">${number('gradient.angle','각도',g.angle,-360,360,.1)}${number('gradient.scale','배율 %',g.scale,.1,1000,.1)}${number('gradient.offset_x','가로 위치 %',g.offset_x,-100,100,.1)}${number('gradient.offset_y','세로 위치 %',g.offset_y,-100,100,.1)}</div><label><input data-prop="gradient.reverse" type="checkbox" ${g.reverse?'checked':''}>반전</label>`;g.colors.forEach((s,i)=>html+=`<div class="lab-property-grid">${color(`gradient.colors.${i}.color`,`색상 ${i+1}`,s.color)}${number(`gradient.colors.${i}.position`,'위치',s.position,0,1,.01)}${number(`gradient.colors.${i}.midpoint`,'중간점',s.midpoint,.01,.99,.01)}</div>`);g.alphas.forEach((s,i)=>html+=`<div class="lab-property-grid">${number(`gradient.alphas.${i}.opacity`,`투명도 ${i+1}`,s.opacity,0,1,.01)}${number(`gradient.alphas.${i}.position`,'위치',s.position,0,1,.01)}${number(`gradient.alphas.${i}.midpoint`,'중간점',s.midpoint,.01,.99,.01)}</div>`);}
    if(['pixel','smartobject'].includes(l.kind))html+='<label>사진 교체<input id="lab-replace-file" type="file" accept="image/png,image/jpeg,image/webp"></label><div class="lab-property-grid">'+number('crop_x','가로 초점',e.crop_x??.5,0,1,.01)+number('crop_y','세로 초점',e.crop_y??.5,0,1,.01)+number('crop_zoom','사진 확대',e.crop_zoom??1,1,5,.1)+'</div><p>사진 교체 후 초점과 확대 비율을 조절하세요.</p>';
    html+='<button id="lab-reset-layer" type="button">레이어 변경 취소</button>';
    el('lab-properties').innerHTML=html;
    if(v.locked)el('lab-properties').querySelectorAll('input,textarea,select,button').forEach(e=>e.disabled=e.dataset.prop!=='locked');
  }
  function outline(){const l=layer(),box=el('lab-selection');if(!l||edits[selected]?.deleted){box.hidden=true;return;}const e=edits[selected]||{},b=bounds[selected]||[l.x,l.y,l.width,l.height];box.hidden=el('lab-original').checked||!(e.visible??l.visible);box.style.cssText=`left:${(e.x??b[0])/doc.size[0]*100}%;top:${(e.y??b[1])/doc.size[1]*100}%;width:${(e.width??b[2])/doc.size[0]*100}%;height:${(e.height??b[3])/doc.size[1]*100}%;`;}
  function select(id){selected=id;el('lab-delete-layer').disabled=!id||Boolean(values().locked);rows();properties();outline();}
  function zoom(){if(!doc)return;const value=el('lab-zoom').value;const viewport=el('lab-viewport');const fit=Math.min(viewport.clientWidth-32,(viewport.clientHeight-32)*doc.size[0]/doc.size[1]);el('lab-stage').style.width=`${value==='fit'?Math.max(120,fit):doc.size[0]*Number(value)}px`;el('lab-stage').style.maxWidth='none';}
  async function preview(){if(!doc||busy)return;const token=++previewToken;try{const r=await fetch(`${path()}/preview`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({changes:edits,order})});if(!r.ok){const d=await r.json();throw Error(typeof d.detail==='string'?d.detail:JSON.stringify(d.detail));}const blob=await r.blob();if(token!==previewToken)return;if(imageURL)URL.revokeObjectURL(imageURL);imageURL=URL.createObjectURL(blob);el('lab-preview').src=imageURL;bounds=JSON.parse(r.headers.get('X-Layer-Bounds')||'{}');outline();const overflow=Object.values(bounds).some(([x,y,w,h])=>x<0||y<0||x+w>doc.size[0]||y+h>doc.size[1]);status(overflow?'미저장 · 캔버스 밖으로 나가는 레이어가 있습니다.':'미리보기 완료 · 저장하지 않은 변경');}catch(e){if(token===previewToken)status(e.message);}}
  function roleChanged(){const role=el('lab-role').value;document.querySelectorAll('[data-mapping]').forEach(e=>e.hidden=role==='cta'||(e.hasAttribute('data-cover')&&role!=='cover'));}
  function show(data){previewToken++;doc=data;template=data.id;revision=data.revision||0;dirty=false;edits=structuredClone(data.edits?.changes||{});order=[...(data.edits?.order||data.layers.map(l=>l.id))];undo=[];redo=[];bounds={};selected=data.layers.find(l=>l.text_editable&&!edits[l.id]?.deleted)?.id||[...order].reverse().find(id=>!edits[id]?.deleted)||null;el('lab-editor').hidden=false;el('lab-document-name').textContent=data.name;el('lab-stage').style.aspectRatio=data.size.join(' / ');el('lab-reference').src=`${path()}/images/reference.png`;el('lab-preview').src=`${path()}/images/preview.png?t=${Date.now()}`;el('lab-download').href=`${path()}/download`;el('lab-comparison').textContent=`${data.size.join(' × ')} · ${data.layers.length}개 레이어 · 최초 변환 오차 ${data.comparison.rgb_mae_0_255.toFixed(2)} / 255`;el('lab-warnings').innerHTML=data.warnings.map(w=>`<li>${esc(w)}</li>`).join('');el('lab-original').checked=false;el('lab-reference').hidden=true;el('lab-preview').hidden=false;el('lab-text-layer').innerHTML='<option value="">선택하세요</option>'+data.layers.filter(l=>l.text_editable).map(l=>`<option value="${l.id}">${esc(l.name)}</option>`).join('');el('lab-image-layer').innerHTML='<option value="">선택하세요</option>'+data.layers.filter(l=>['pixel','smartobject'].includes(l.kind)).map(l=>`<option value="${l.id}">${esc(l.name)}</option>`).join('');const textOptions='<option value="">사용 안 함</option>'+data.layers.filter(l=>l.text_editable).map(l=>`<option value="${l.id}">${esc(l.name)}</option>`).join('');el('lab-eyebrow-layer').innerHTML=textOptions;el('lab-caption-layer').innerHTML=textOptions;el('lab-badge-layer').innerHTML='<option value="">사용 안 함</option>'+data.layers.filter(l=>['shape','solidcolorfill','pixel'].includes(l.kind)).map(l=>`<option value="${l.id}">${esc(l.name)}</option>`).join('');roleChanged();if(data.role)el('lab-role').value=data.role;restoreMapping();roleChanged();rows();properties();zoom();outline();draftState();el('pc-library')?.close();status('편집용 초안을 열었습니다. 채널 적용 버전은 별도로 보존됩니다.');}
  async function loadCatalog(){catalog=await api(`${base()}/catalog`);el('studio-slots').innerHTML=['cover','body','cta'].map(role=>{const a=catalog.active[role];return `<article class="studio-slot" data-module="${role}"><button class="studio-module-open" type="button" data-open-role="${role}"><strong>${{cover:'표지',body:'본문',cta:'CTA'}[role]}</strong><span>${esc(a?.name||a?.template_id||'미지정')}</span><small>${a?.renderer==='template-lab'?`적용 버전 · ${a.draft_matches_active===true?'초안과 동일':a.draft_matches_active===false?'초안에 미적용 변경 있음':'연결 초안 열기'}`:a?.template_id?'기존 PSD 사용 중':'템플릿을 적용하세요'}</small></button>${role==='cover'&&a?.renderer==='template-lab'&&(a.accent_layer||a.text_options?.eyebrow?.align_to_layer)?`<label class="studio-ai-accent"><input id="lab-ai-accent" type="checkbox" ${a.ai_accent?'checked':''}> AI가 기사마다 배지 색 변경 <small>(끄면 템플릿에서 고른 색 사용)</small></label><small class="studio-ai-target">AI 색 적용 레이어: <b>${esc(a.accent_layer_name||'')}</b> · 배지 글자는 흰색/검정 자동</small>`:''}<details><summary>이전 버전</summary>${catalog.history.filter(h=>h.role===role).map(h=>`<p>${esc(h.spec.name)} · ${esc(h.spec.template_id.slice(0,8))} <button type="button" data-restore="${h.spec.template_id}" data-role="${role}" ${a?.template_id===h.spec.template_id?'disabled':''}>${a?.template_id===h.spec.template_id?'사용 중':'이 버전 적용'}</button></p>`).join('')||'보관된 편집기 버전 없음'}</details></article>`;}).join('');
    el('lab-applied-list').innerHTML=['cover','body','cta'].map(role=>{const a=catalog.active[role];return `<button type="button" data-open-role="${role}">${{cover:'표지',body:'본문',cta:'CTA'}[role]} · ${esc(a?.name||a?.template_id||'미지정')} — ${a?.draft_id?`연결 초안 열기 (${a.draft_matches_active===true?'적용본과 동일':a.draft_matches_active===false?'미적용 변경 있음':'내용 확인'})`:'PSD 가져오기'}</button>`;}).join('');draftState();
  }
  async function library(chosen=''){previewToken++;doc=null;template='';el('lab-editor').hidden=true;const list=await api(base());el('lab-library').innerHTML='<option value="">초안 선택</option>'+list.map(t=>`<option value="${t.id}">${esc(t.name)} · ${t.id.slice(0,8)}</option>`).join('');const id=chosen;el('lab-library').value=id;if(id)show(await api(`${base()}/${id}`));}
  async function run(action){if(busy)return;lock(true);try{await action();}catch(e){status(e.message);}finally{lock(false);if(doc)properties();}}
  el('lab-channel').onchange=()=>{const next=el('lab-channel').value;if(!discard()){el('lab-channel').value=channel;return;}run(async()=>{channel=next;dirty=false;await loadCatalog();await library();});};
  el('lab-open-draft').onclick=()=>{const next=el('lab-library').value;if(!next){status('열 초안을 선택하세요.');return;}if(!discard()){el('lab-library').value=template;return;}run(async()=>{dirty=false;if(next)show(await api(`${base()}/${next}`));else{doc=null;template='';el('lab-editor').hidden=true;}});};
  el('lab-upload').onsubmit=event=>{event.preventDefault();if(!discard())return;run(async()=>{const file=el('lab-file').files[0];if(!file)return;const form=new FormData();form.append('file',file);status('PSD 레이어를 읽고 있습니다…');const data=await api(base(),{method:'POST',body:form});await library(data.id);});};
  el('lab-layers').onclick=event=>{const id=event.target.closest('[data-select]')?.dataset.select;if(id&&!busy)select(id);};
  el('lab-layers').onchange=event=>{const id=event.target.dataset.visible;if(!id||busy)return;if(edits[id]?.locked){rows();return;}remember();edits[id]={...edits[id],visible:event.target.checked};outline();changed();};
  let dragged;
  el('lab-layers').ondragstart=event=>{dragged=event.target.closest('[data-layer]')?.dataset.layer;if(busy||edits[dragged]?.locked)event.preventDefault();};
  el('lab-layers').ondragover=event=>event.preventDefault();
  el('lab-layers').ondrop=event=>{event.preventDefault();const id=event.target.closest('[data-layer]')?.dataset.layer;if(!id||!dragged||id===dragged||busy)return;remember();order.splice(order.indexOf(dragged),1);order.splice(order.indexOf(id),0,dragged);rows();changed();};
  el('lab-properties').onfocusin=()=>{focusState=snapshot();};
  el('lab-properties').oninput=event=>{const input=event.target,key=input.dataset.prop;if(input.matches('[data-color-hex],[data-color-rgb]')){editColor(input);return;}if(!key||busy||!input.checkValidity()||(values().locked&&key!=='locked'))return;const value=input.type==='checkbox'?input.checked:input.type==='number'?Number(input.value):input.value;const e=edits[selected]||(edits[selected]={});if(['width','height'].includes(key)){const dimensions=resizeDimensions(values(),key,value,keepRatio);if(!dimensions){status('비율 유지 크기 범위는 1~4096px입니다.');return;}Object.assign(e,dimensions);for(const dimension of ['width','height'])el('lab-properties').querySelector(`[data-prop="${dimension}"]`).value=e[dimension]??values()[dimension];}else if(key==='blend_mode'&&!value){delete e.blend_mode;}else if(key==='font_id'&&!value){delete e.font_id;delete e.font_file;}else if(key.includes('.')){const parts=key.split('.');if(!e[parts[0]])e[parts[0]]=structuredClone(layer()[parts[0]]||{});if(parts[0]==='text_effects'&&!e.text_effects[parts[1]])e.text_effects[parts[1]]=parts[1]==='shadow'?{color:'#000000',opacity:0,distance:4,angle:90,blur:4}:{color:'#000000',width:0,opacity:1};let dst=e;for(const p of parts.slice(0,-1))dst=dst[p];dst[parts.at(-1)]=value;}else e[key]=value;if(input.type==='color')syncColor(input);outline();changed();};
  el('lab-properties').onchange=event=>{if(event.target.id==='lab-keep-ratio'){keepRatio=event.target.checked;return;}if(event.target.dataset.prop){if(focusState)remember(focusState);focusState=snapshot();if(event.target.dataset.prop==='locked'){rows();properties();}return;}if(event.target.id!=='lab-replace-file')return;const file=event.target.files[0];if(!file)return;const id=selected;run(async()=>{const form=new FormData();form.append('file',file);const result=await api(`${path()}/replace/${id}`,{method:'POST',body:form});remember();edits[id]={...edits[id],asset:result.asset};changed();}).then(preview);};
  el('lab-properties').onclick=event=>{if(busy||values().locked)return;const b=event.target.closest('button');if(!b)return;if(b.dataset.colorPreset){editColor(b,b.dataset.colorPreset);return;}if(b.id==='lab-use-runs'){remember();for(const k of ['font_id','font_file','font_size','text_color'])delete edits[selected]?.[k];}else if(b.dataset.order){const i=order.indexOf(selected),j=i+Number(b.dataset.order);if(j<0||j>=order.length)return;remember();[order[i],order[j]]=[order[j],order[i]];}else if(b.dataset.align){remember();edits[selected]={...edits[selected],...alignLayer(b.dataset.align)};}else if(b.id==='lab-reset-layer'){remember();delete edits[selected];}else return;rows();properties();outline();changed();};
  function history(from,to){if(!from.length||busy)return;to.push(snapshot());const data=JSON.parse(from.pop());edits=data.edits;order=data.order;selected=order.find(id=>!edits[id]?.deleted)||null;rows();properties();outline();changed();}
  el('lab-undo').onclick=()=>history(undo,redo);el('lab-redo').onclick=()=>history(redo,undo);
  el('lab-original').onchange=()=>{el('lab-reference').hidden=!el('lab-original').checked;el('lab-preview').hidden=el('lab-original').checked;outline();};
  el('lab-zoom').onchange=zoom;el('lab-guides').onchange=()=>document.querySelector('.studio-safe').hidden=!el('lab-guides').checked;
  let tool='move';
  el('lab-stage').addEventListener('studio-tool',event=>{
    tool=event.detail;
    if(!doc||busy)return;
    const keys={text:'text',photo:'photo',transform:'width',crop:'crop_zoom',shape:'shape.fill',gradient:'gradient.angle'};
    const key=keys[tool];
    if(!key)return;
    const matches=l=>l&&!edits[l.id]?.deleted&&(tool==='text'?l.text_editable:tool==='shape'?l.shape:tool==='gradient'?l.gradient:['photo','crop'].includes(tool)?['pixel','smartobject'].includes(l.kind):true);
    if(!matches(layer())||values().locked){const candidate=[...doc.layers].reverse().find(l=>matches(l)&&!({...l,...edits[l.id]}).locked);if(candidate)select(candidate.id);}
    const input=key==='photo'?el('lab-replace-file'):el('lab-properties').querySelector(`[data-prop="${key}"]`);
    if(!input||input.disabled){status('이 도구를 사용할 수 있는 잠금 해제된 레이어가 없습니다.');return;}
    input.scrollIntoView({block:'nearest'});input.focus();
    if(tool==='photo')input.click();
    else status(tool==='crop'?'교체한 사진의 초점·확대를 조절하세요. 캔버스의 선택 테두리를 드래그하면 사진 초점이 바뀝니다.':'선택 레이어의 속성을 편집하세요. 변경은 초안에만 반영됩니다.');
  });
  el('lab-stage').addEventListener('click',event=>{
    if(tool!=='eyedropper'||busy||!doc)return;
    event.preventDefault();event.stopImmediatePropagation();
    const key=layer()?.text_editable?'text_color':layer()?.shape?'shape.fill':null;
    if(!key||values().locked){status('색상을 적용할 글자 또는 도형 레이어를 먼저 선택하세요.');return;}
    const img=el(el('lab-original').checked?'lab-reference':'lab-preview');
    if(!img.complete||!img.naturalWidth)return;
    const rect=el('lab-stage').getBoundingClientRect(),canvas=document.createElement('canvas');
    canvas.width=img.naturalWidth;canvas.height=img.naturalHeight;
    const ctx=canvas.getContext('2d',{willReadFrequently:true});ctx.drawImage(img,0,0);
    const x=Math.max(0,Math.min(canvas.width-1,Math.floor((event.clientX-rect.left)/rect.width*canvas.width))),y=Math.max(0,Math.min(canvas.height-1,Math.floor((event.clientY-rect.top)/rect.height*canvas.height)));
    const rgba=ctx.getImageData(x,y,1,1).data;
    if(!rgba[3]){status('투명한 영역입니다. 다른 지점을 선택하세요.');return;}
    const color='#'+[...rgba].slice(0,3).map(v=>v.toString(16).padStart(2,'0')).join('');
    remember();const e=edits[selected]||{};
    edits[selected]=key==='text_color'?{...e,text_color:color}:{...e,shape:{...values().shape,fill:color}};
    properties();changed();
  },true);
  el('lab-stage').onclick=event=>{if(busy||el('lab-original').checked||event.target.closest('#lab-selection'))return;const rect=el('lab-stage').getBoundingClientRect(),x=(event.clientX-rect.left)/rect.width*doc.size[0],y=(event.clientY-rect.top)/rect.height*doc.size[1];const id=[...order].reverse().find(id=>{const l=doc.layers.find(l=>l.id===id),v={...l,...edits[id]},b=bounds[id]||[v.x,v.y,v.width,v.height];return v.visible&&!v.locked&&x>=b[0]&&y>=b[1]&&x<=b[0]+b[2]&&y<=b[1]+b[3];});if(id)select(id);};
  let drag=null;
  el('lab-selection').onpointerdown=event=>{if(busy||values().locked||['eyedropper','zoom','hand'].includes(tool))return;if(tool==='crop'&&!edits[selected]?.asset?.startsWith('layers/replacement-')){status('사진을 먼저 교체한 뒤 자르기를 사용하세요.');return;}event.preventDefault();remember();const v=values(),group=el('lab-group-move')?.checked;const members=group?doc.layers.filter(l=>JSON.stringify(l.group_path)===JSON.stringify(layer().group_path)&&!edits[l.id]?.locked):[layer()];drag={crop:tool==='crop',cx:v.crop_x??.5,cy:v.crop_y??.5,sx:event.clientX,sy:event.clientY,w:v.width,h:v.height,resize:event.target.id==='lab-resize',members:members.map(l=>({id:l.id,x:edits[l.id]?.x??l.x,y:edits[l.id]?.y??l.y}))};event.currentTarget.setPointerCapture(event.pointerId);el('lab-stage').focus();};
  el('lab-selection').onpointermove=event=>{if(!drag)return;const ratio=doc.size[0]/el('lab-stage').getBoundingClientRect().width,dx=(event.clientX-drag.sx)*ratio,dy=(event.clientY-drag.sy)*ratio;if(drag.crop){edits[selected]={...edits[selected],crop_x:Math.max(0,Math.min(1,drag.cx-dx/drag.w)),crop_y:Math.max(0,Math.min(1,drag.cy-dy/drag.h))};}else if(drag.resize){const f=Math.max(1/Math.min(drag.w,drag.h),Math.min(4096/Math.max(drag.w,drag.h),(drag.w+dx)/drag.w));edits[selected]={...edits[selected],width:Math.round(drag.w*f),height:Math.round(drag.h*f)};}else for(const l of drag.members)edits[l.id]={...edits[l.id],x:Math.max(-10000,Math.min(10000,Math.round(l.x+dx))),y:Math.max(-10000,Math.min(10000,Math.round(l.y+dy)))};outline();};
  el('lab-selection').onpointerup=el('lab-selection').onpointercancel=()=>{if(!drag)return;drag=null;properties();changed();};
  el('lab-stage').onkeydown=event=>{if(busy||!selected)return;if((event.ctrlKey||event.metaKey)&&event.key.toLowerCase()==='z'){event.preventDefault();history(event.shiftKey?redo:undo,event.shiftKey?undo:redo);return;}if(values().locked)return;const d={ArrowLeft:[-1,0],ArrowRight:[1,0],ArrowUp:[0,-1],ArrowDown:[0,1]}[event.key];if(!d)return;event.preventDefault();remember();const v=values(),step=event.shiftKey?10:1;edits[selected]={...edits[selected],x:v.x+d[0]*step,y:v.y+d[1]*step};properties();outline();changed();};
  function restoreMapping(){
    const role=el('lab-role').value, current=catalog.active[role];
    const active=current&&doc&&current.draft_id===doc.id?current:{};
    const field=role==='cover'?'title':'body', mapping=active.layers||{}, options=active.text_options||{};
    for(const [id,value] of Object.entries({'lab-text-layer':mapping[field]||'', 'lab-image-layer':active.image_layer||'', 'lab-eyebrow-layer':mapping.eyebrow||'', 'lab-badge-layer':options.eyebrow?.align_to_layer||'', 'lab-caption-layer':role==='cover'?mapping.body||'':''}))el(id).value=value;
    for(const [id,key,fallback] of [['lab-max-chars','max_chars',80],['lab-line-chars','line_chars',20],['lab-max-lines','max_lines',4]])el(id).value=options[field]?.[key]??fallback;
  }
  async function save(reset=false){if(!doc)return;if(reset){remember();edits={};order=doc.layers.map(l=>l.id);dirty=true;}clearTimeout(timer);previewToken++;const r=await api(`${path()}/render`,{method:'POST',body:JSON.stringify({changes:edits,order,revision})});revision=r.revision;bounds=r.bounds||{};dirty=false;el('lab-preview').src=`${path()}/images/preview.png?t=${Date.now()}`;await loadCatalog();status('초안 저장 완료 · 채널에 적용해야 제작에 반영됩니다.');rows();outline();}
  el('lab-render').onclick=()=>run(()=>save());el('lab-reset').onclick=()=>{if(confirm('모든 레이어를 원본 설정으로 되돌리고 초안에 저장할까요?'))run(()=>save(true));};
  el('lab-role').onchange=()=>{restoreMapping();roleChanged();};
  el('lab-publish').onclick=()=>run(async()=>{if(dirty)throw Error('변경 내용을 먼저 초안에 저장하세요.');const role=el('lab-role').value;const payload={role,revision,expected_active:catalog.active[role]?.template_id||'',text_layer:el('lab-text-layer').value,image_layer:el('lab-image-layer').value,max_chars:Number(el('lab-max-chars').value),line_chars:Number(el('lab-line-chars').value),max_lines:Number(el('lab-max-lines').value),eyebrow_layer:el('lab-eyebrow-layer').value,badge_layer:el('lab-badge-layer').value,caption_layer:el('lab-caption-layer').value};await api(`${path()}/publish`,{method:'POST',body:JSON.stringify(payload)});await loadCatalog();status('새 버전을 보관하고 채널에 적용했습니다. 이후 새 제작부터 사용합니다.');});
  el('studio-slots').onchange=event=>{if(event.target.id!=='lab-ai-accent')return;const enabled=event.target.checked;run(async()=>{await api(`${base()}/ai-accent`,{method:'POST',body:JSON.stringify({role:'cover',enabled,expected_active:catalog.active.cover?.template_id||''})});await loadCatalog();if(doc)rows();status(enabled?'다음 제작부터 AI가 기사마다 배지 색을 고릅니다.':'다음 제작부터 템플릿에서 고른 배지 색을 사용합니다.');});};
  async function openModule(role){
    if(busy||!discard())return;
    const active=catalog.active[role];
    if(!active?.draft_id){
      el('lab-role').value=role;
      restoreMapping();roleChanged();
      status('이 역할의 PSD를 가져오거나 보관함에서 초안을 선택한 뒤 채널에 적용하세요.');
      if(!el('pc-library')?.open)el('pc-library')?.showModal();
      return;
    }
    await run(async()=>{
      const data=await api(`${base()}/${encodeURIComponent(active.draft_id)}`);
      show({...data,role});
      el('lab-library').value=data.id;
      document.querySelectorAll('[data-open-role]').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.openRole===role)));
      status('현재 적용 버전에 연결된 초안을 열었습니다. 초안의 미적용 변경이 포함될 수 있습니다.');
    });
  }
  el('lab-applied-list').onclick=event=>{const button=event.target.closest('[data-open-role]');if(button)openModule(button.dataset.openRole);};
  el('studio-slots').onclick=event=>{
    const module=event.target.closest('[data-open-role]');
    if(module){openModule(module.dataset.openRole);return;}
    const button=event.target.closest('[data-restore]');
    if(!button||busy||!discard())return;
    run(async()=>{
      const role=button.dataset.role;
      await api(`${base()}/activate/${button.dataset.restore}`,{method:'POST',body:JSON.stringify({role,revision:0,expected_active:catalog.active[role]?.template_id||''})});
      await loadCatalog();
      if(doc){restoreMapping();rows();}
      status('보관 버전을 다시 적용했습니다. 모듈을 눌러 연결된 초안을 확인하세요.');
    });
  };
  window.addEventListener('resize',zoom);
  window.addEventListener('beforeunload',event=>{if(dirty){event.preventDefault();event.returnValue='';}});
  async function initialize(){
    const profiles=await api('/api/template-lab/channels');
    el('lab-channel').innerHTML=profiles.map(p=>`<option value="${esc(p.id)}">${esc(p.name)}</option>`).join('');
    if(!profiles.length){status('편집국에서 채널을 먼저 만드세요.');return;}
    const query=new URLSearchParams(location.search), requested=query.get('channel');
    if(requested&&!profiles.some(p=>p.id===requested))throw Error('요청한 채널을 찾을 수 없습니다.');
    channel=requested||profiles[0].id;
    el('lab-channel').value=channel;
    fonts=await api('/api/template-lab/fonts');
    await loadCatalog();
    await library(requested ? query.get('draft')||'' : '');
    if(!doc)status('채널을 불러왔습니다. 신규 PSD 또는 저장된 초안을 선택하세요.');
  }
  run(initialize);
  el('lab-layer-search').oninput=event=>{layerQuery=event.target.value.toLowerCase();if(doc)rows();};
  el('lab-layer-kind').onchange=event=>{layerKind=event.target.value;if(doc)rows();};
})();
