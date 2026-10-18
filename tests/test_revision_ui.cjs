// Run with: node tests/test_revision_ui.cjs. No browser or network required.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const elements = new Map();
const groups = new Map();
function element() {
  let value = '';
  return {
    dataset: {}, files: [], disabled: false, open: false,
    classList: { toggle() {}, add() {}, remove() {} },
    set value(v) { value = v; if (v === '') this.files = []; },
    get value() { return value; },
    showModal() { this.open = true; }, close() { this.open = false; },
    set innerHTML(html) {
      for (const [attribute, key] of [['edit-slide','editSlide'], ['preview-artifact','previewArtifact']]) {
        const matches = [...html.matchAll(new RegExp(`data-${attribute}="(\\d+)"`, 'g'))];
        if (matches.length) groups.set(`[data-${attribute}]`, matches.map(m => ({...element(), dataset: {[key]:m[1]}})));
      }
    },
    get innerHTML() { return this.textContent || ''; }
  };
}
const get = selector => {
  if (!elements.has(selector)) elements.set(selector, element());
  return elements.get(selector);
};
const form = get('#revision-form');
form.elements = Object.fromEntries(['slide_number','slide_title','slide_body','cover_title','instruction','instruction_target','reference_image'].map(k => [k, element()]));
form.reset = () => Object.values(form.elements).forEach(e => e.value = '');
const pending = [];
const context = vm.createContext({
  document: {querySelector:get, querySelectorAll:s => groups.get(s) || [], createElement:element, addEventListener() {}},
  FormData: class {},
  DataTransfer: class { constructor() { this.files=[]; this.items={add:f => this.files.push(f)}; } },
  fetch: url => new Promise((resolve,reject) => pending.push({url, resolve:data => resolve({ok:true,json:async()=>data}), reject})),
  setTimeout: () => 0, clearTimeout() {}, console
});
vm.runInContext(fs.readFileSync(path.join(__dirname, '../src/cardnews_auto/web/static/ui.js'), 'utf8'), context);
vm.runInContext(`state.data = {profiles:[{id:'test', artifacts:[
  {id:1,magazine_id:'test',topic:'Original',built_at:'2026-10-13'},
  {id:2,parent_artifact_id:1,magazine_id:'test',topic:'Revision',built_at:'2026-10-13'}
]}]}`, context);
const detail = id => ({slides:[1,2,3].map(number => ({number,role:number===1?'cover':'body',title:`${id}-${number}`,body:'Body',image_url:`/${id}/${number}.jpg`}))});
const slide = n => groups.get('[data-edit-slide]')[n-1].onclick();
const version = n => groups.get('[data-preview-artifact]')[n-1].onclick();

(async () => {
  const opened = context.openRevision(1);
  assert.equal(get('#submit-revision').disabled, true);
  pending.shift().resolve(detail(1)); await opened;
  form.elements.slide_title.value = 'Edited';
  form.elements.instruction.value = 'Only first card';
  form.elements.reference_image.files = [{name:'first.png'}];
  slide(2);
  assert.equal(get('#revision-cover').src, '/1/2.jpg');
  assert.equal(form.elements.instruction.value, '');
  assert.equal(form.elements.reference_image.files.length, 0);
  slide(1);
  assert.equal(form.elements.slide_title.value, 'Edited');
  assert.equal(form.elements.instruction.value, 'Only first card');
  assert.equal(form.elements.reference_image.files[0].name, 'first.png');
  console.log('PASS: slide navigation preserves drafts and isolates reference images');

  const loading = version(2);
  slide(2);
  assert.equal(get('#submit-revision').disabled, true);
  assert.equal(form.elements.slide_number.value, 1);
  assert.equal(vm.runInContext('state.editingArtifact.id', context), 1);
  pending.shift().resolve(detail(2)); await loading;
  assert.equal(get('#submit-revision').disabled, false);
  console.log('PASS: card clicks cannot unlock submission during version loading');

  vm.runInContext('state.revisionSubmitting = true', context);
  slide(2);
  await version(1);
  assert.equal(form.elements.slide_number.value, 1);
  assert.equal(pending.length, 0);
  vm.runInContext('state.revisionSubmitting = false', context);
  console.log('PASS: card and version changes are blocked during submission');

  const older = version(1), newer = version(2);
  const oldRequest = pending.shift(), newRequest = pending.shift();
  newRequest.resolve(detail(2)); await newer;
  oldRequest.resolve(detail(1)); await older;
  assert.equal(get('#revision-cover').src, '/2/1.jpg');
  assert.equal(vm.runInContext('state.editingArtifact.id',context), 2);
  assert.equal(get('#submit-revision').disabled, false);
  console.log('PASS: out-of-order responses cannot replace the selected version');

  const failed = version(1);
  pending.shift().reject(new Error('offline')); await failed;
  assert.equal(get('#revision-cover').src, '/2/1.jpg');
  assert.equal(vm.runInContext('state.editingArtifact.id',context), 2);
  console.log('PASS: failed version loading retains the displayed artifact');

  const empty = version(1); pending.shift().resolve({slides:[]}); await empty;
  assert.equal(vm.runInContext('state.editingArtifact.id',context), 2);
  console.log('PASS: empty slide response is handled');

  const closing = version(1); get('#revision-dialog').close();
  pending.shift().resolve(detail(1)); await closing;
  assert.equal(vm.runInContext('state.editingArtifact.id',context), 2);
  console.log('PASS: closed editor ignores late responses');
})().catch(error => { console.error(error); process.exitCode=1; });
