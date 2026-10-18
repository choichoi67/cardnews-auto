const {test} = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

test('draft opening and canvas tools preserve undo and save through the editor', async () => {
  const elements = new Map(), requests = [];
  const shape = {fill:'#ffffff',stroke:'#000000',stroke_width:0};
  const draft = {id:'a'.repeat(32),name:'sample',size:[1080,1440],revision:0,
    comparison:{rgb_mae_0_255:0},warnings:[],edits:{changes:{photo:{asset:'layers/replacement-'+'b'.repeat(32)+'.png'}}},
    layers:[{id:'shape',name:'Shape',kind:'shape',visible:true,x:0,y:0,width:100,height:100,shape},
      {id:'photo',name:'Photo',kind:'pixel',visible:true,x:0,y:0,width:200,height:200}]};
  function el(id) {
    if (!elements.has(id)) elements.set(id, {
      id, hidden:id==='lab-editor', value:id==='lab-zoom'?'fit':'', checked:false,
      textContent:'',innerHTML:'',style:{},dataset:{},disabled:false,listeners:{},
      clientWidth:800,clientHeight:800,complete:true,naturalWidth:1080,naturalHeight:1440,
      addEventListener(name, fn) {this.listeners[name]=fn;},
      querySelectorAll() {return [];},querySelector(selector) {return el(selector);},
      setAttribute() {},focus() {},scrollIntoView() {},click() {},close() {this.closed=true;},
      setPointerCapture() {},getBoundingClientRect() {return {left:0,top:0,width:1080,height:1440};},
    });
    return elements.get(id);
  }
  const document = {getElementById:el,querySelector:el,querySelectorAll:()=>[],
    createElement:()=>({getContext:()=>({drawImage() {},getImageData:()=>({data:[18,52,86,255]})})})};
  const api = async (url, options={}) => {
    requests.push([url, options]);
    if(url.endsWith('/channels'))return [{id:'a',name:'A'}];
    if(url.endsWith('/fonts'))return [];
    if(url.endsWith('/catalog'))return {active:{},history:[]};
    if(url.endsWith('/render'))return {revision:1,bounds:{}};
    if(url.endsWith('/'+draft.id))return structuredClone(draft);
    return [{id:draft.id,name:draft.name}];
  };
  vm.runInNewContext(readFileSync(path.join(__dirname,'../src/cardnews_auto/web/static/template-studio.js'),'utf8'), {
    document,api,escapeHtml:String,structuredClone,window:{addEventListener() {}},
    setTimeout:()=>1,clearTimeout() {},confirm:()=>true,URL,FormData,
  });
  const settle=()=>new Promise(resolve=>setImmediate(resolve));
  await settle();
  assert.equal(el('lab-editor').hidden,true,'a single draft must not open automatically');
  el('lab-library').value=draft.id;
  el('lab-open-draft').onclick();await settle();
  assert.equal(el('lab-editor').hidden,false);
  assert.equal(el('pc-library').closed,true);
  const stage=el('lab-stage'), selection=el('lab-selection');
  stage.listeners['studio-tool']({detail:'crop'});
  selection.onpointerdown({clientX:0,clientY:0,target:{id:''},currentTarget:selection,preventDefault() {}});
  selection.onpointermove({clientX:40,clientY:-20});selection.onpointerup();
  el('lab-render').onclick();await settle();
  let saved=JSON.parse(requests.filter(([url])=>url.endsWith('/render')).at(-1)[1].body);
  assert.equal(saved.changes.photo.crop_x,.3);
  assert.equal(saved.changes.photo.crop_y,.6);
  el('lab-undo').onclick();el('lab-render').onclick();await settle();
  saved=JSON.parse(requests.filter(([url])=>url.endsWith('/render')).at(-1)[1].body);
  assert.equal(saved.changes.photo.crop_x,undefined);
  stage.listeners['studio-tool']({detail:'shape'});
  stage.listeners['studio-tool']({detail:'eyedropper'});
  stage.listeners.click({clientX:10,clientY:10,preventDefault() {},stopImmediatePropagation() {}});
  el('lab-render').onclick();await settle();
  saved=JSON.parse(requests.filter(([url])=>url.endsWith('/render')).at(-1)[1].body);
  assert.equal(saved.changes.shape.shape.fill,'#123456');
  assert.ok(!requests.some(([url])=>url.endsWith('/publish')),'draft save must never activate a channel version');
});
