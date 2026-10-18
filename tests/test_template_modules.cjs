const assert = require('node:assert/strict');
const {test} = require('node:test');
const {readFileSync} = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

test('channel PSD upload retries keep the saved channel and completed drafts', async () => {
  const source = readFileSync(path.join(__dirname, '../src/cardnews_auto/web/static/app.js'), 'utf8');
  const start = source.indexOf('$("#profile-form").onsubmit =');
  const end = source.indexOf('if (location.hash', start);
  const inputs = ['cover', 'body', 'cta'].map(role => ({
    dataset: {psdRole: role}, files: [{name: `${role}.psd`}],
    set value(value) { if (!value) this.files = []; },
  }));
  const form = {checkValidity: () => true, elements: {}, querySelectorAll: selector => selector === '[data-psd-role]' ? inputs : []};
  const elements = new Map([['#profile-form', form]]), calls = [];
  let fail = true;
  const context = {
    $: id => { if (!elements.has(id)) elements.set(id, {}); return elements.get(id); },
    state: {pendingPsdFiles: []}, PROFILE_CHECKBOXES: [], URLSearchParams,
    FormData: class extends Map {append(key, value) {this.set(key, value);} *[Symbol.iterator]() {yield* this.entries();}},
    document: {querySelectorAll: () => []}, location: {},
    lines: () => [], setButtonLoading() {}, toast() {}, refresh: async () => {},
    api: async (url, options) => {
      calls.push([url, options.method]);
      if (url.startsWith('/api/profiles')) return {id: 'new-channel'};
      const role = options.body.get('role');
      if (role === 'body' && fail) {fail = false; throw Error('upload failed');}
      return {id: `${role}-draft`};
    },
  };
  // FormData(form) supplies no profile fields in this focused orchestration check.
  context.FormData = class extends context.FormData {constructor() {super();}};
  vm.createContext(context);
  vm.runInContext(source.slice(start, end), context);
  const event = {preventDefault() {}, currentTarget: form, submitter: {dataset: {}}};
  await form.onsubmit(event);
  assert.equal(context.state.editingId, 'new-channel');
  assert.deepEqual(context.state.pendingPsdFiles, ['cover-draft']);
  assert.equal(context.location.href, undefined);
  await form.onsubmit(event);
  assert.equal(calls.filter(([url, method]) => url === '/api/profiles' && method === 'POST').length, 1);
  assert.ok(calls.some(([url, method]) => url === '/api/profiles/new-channel' && method === 'PUT'));
  assert.equal(context.location.href, '/template-lab-photocraft.html?channel=new-channel&draft=cover-draft');
  assert.equal(context.state.profileSaving, false);
});

test('studio opens the requested channel and draft, and rejects unknown channels', async () => {
  const source = readFileSync(path.join(__dirname, '../src/cardnews_auto/web/static/template-studio.js'), 'utf8');
  const start = source.indexOf('  async function initialize()');
  const end = source.indexOf('  run(initialize);', start);
  const elements = new Map(), opened = [];
  const context = {
    URLSearchParams, location: {search: '?channel=second&draft=body-draft'},
    api: async url => url.endsWith('/channels') ? [{id:'first', name:'First'}, {id:'second', name:'Second'}] : [],
    el: id => { if (!elements.has(id)) elements.set(id, {}); return elements.get(id); },
    esc: value => value, status() {}, doc: null,
    loadCatalog: async () => {}, library: async id => opened.push(id),
  };
  vm.createContext(context);
  vm.runInContext(source.slice(start, end), context);
  await context.initialize();
  assert.equal(context.channel, 'second');
  assert.equal(elements.get('lab-channel').value, 'second');
  assert.deepEqual(opened, ['body-draft']);
  context.location.search = '?channel=missing&draft=body-draft';
  await assert.rejects(context.initialize(), /채널을 찾을 수 없습니다/);
  assert.equal(opened.length, 1);
});

test('module selection loads the assigned draft and role, guards edits, and handles empty slots', async () => {
  const source = readFileSync(path.join(__dirname, '../src/cardnews_auto/web/static/template-studio.js'), 'utf8');
  const start = source.indexOf('  async function openModule(role)');
  const end = source.indexOf("  el('studio-slots').onclick", start);
  const elements = new Map();
  const calls = [];
  const context = {
    busy: false, discard: () => true,
    catalog: {active: {cover: {draft_id: 'cover-draft'}, body: {draft_id: 'body-draft'}}},
    base: () => '/api/template-lab/channel-a',
    api: async url => { calls.push(url); return {id: url.split('/').at(-1), role: 'wrong'}; },
    show: data => calls.push(data), run: async action => action(),
    el: id => { if (!elements.has(id)) elements.set(id, {showModal() { this.open = true; }}); return elements.get(id); },
    restoreMapping() {}, roleChanged() {}, status() {},
    document: {querySelectorAll: () => []},
  };
  vm.createContext(context);
  vm.runInContext(source.slice(start, end), context);
  await context.openModule('body');
  assert.equal(calls[0], '/api/template-lab/channel-a/body-draft');
  assert.equal(calls[1].role, 'body');
  assert.equal(elements.get('lab-library').value, 'body-draft');
  context.discard = () => false;
  await context.openModule('cover');
  assert.equal(calls.length, 2);
  context.discard = () => true;
  context.busy = true;
  await context.openModule('cover');
  assert.equal(calls.length, 2);
  context.busy = false;
  await context.openModule('cta');
  assert.equal(elements.get('lab-role').value, 'cta');
  assert.equal(elements.get('pc-library').open, true);
  assert.equal(calls.length, 2);
});
