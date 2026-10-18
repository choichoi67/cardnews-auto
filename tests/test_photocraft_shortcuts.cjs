const assert = require('node:assert/strict');
const {test} = require('node:test');
const {readFileSync} = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

test('shortcuts reuse controls without stealing text, IME, modal or busy input', () => {
  const elements = new Map();
  function element(id) {
    if (!elements.has(id)) elements.set(id, {
      hidden: false, disabled: false, open: false, textContent: 'sample', style: {aspectRatio: '1080 / 1440'},
      clicks: 0, listeners: {}, scrollLeft: 100, scrollTop: 100,
      options: ['fit', '.5', '.75', '1', '1.5'].map(value => ({value})),
      addEventListener(name, handler) { this.listeners[name] = handler; },
      setAttribute() {}, append() {}, scrollIntoView() {}, setPointerCapture() {},
      focus() { this.focused = true; },
      click() { if (!this.disabled) this.clicks++; },
      showModal() { this.open = true; }, close() { this.open = false; },
      dispatchEvent() { this.changed = true; },
      getBoundingClientRect() { return {width: 1080}; },
      querySelector: selector => element(selector),
    });
    return elements.get(id);
  }
  const dialog = element('pc-library');
  const handlers = {}, windowHandlers = {};
  const document = {
    getElementById: element, querySelector: element, createElement: element,
    querySelectorAll: selector => selector === 'dialog' ? [dialog] : [],
    addEventListener: (name, handler) => { handlers[name] = handler; },
  };
  vm.runInNewContext(readFileSync(path.join(__dirname, '../src/cardnews_auto/web/static/template-lab-photocraft.js'), 'utf8'), {
    document, window: {addEventListener: (name, handler) => { windowHandlers[name] = handler; }},
    MutationObserver: class { observe() {} }, Event: class {},
  });
  function press(key, options = {}) {
    const event = {key, target: {closest: () => null}, preventDefault() { this.prevented = true; }, stopPropagation() {}, ...options};
    handlers.keydown(event);
    return event;
  }
  press('s', {ctrlKey: true});
  assert.equal(element('lab-render').clicks, 1);
  press('z', {ctrlKey: true});
  press('z', {metaKey: true, shiftKey: true});
  assert.equal(element('lab-undo').clicks, 1);
  assert.equal(element('lab-redo').clicks, 1);
  assert.equal(press('z', {ctrlKey: true, target: {closest: () => ({})}}).prevented, undefined);
  press('t', {isComposing: true});
  assert.equal(element('[data-prop="text"]').focused, undefined);
  press('t');
  assert.equal(element('[data-prop="text"]').focused, true);
  press('g');
  assert.equal(element('[data-prop="gradient.angle"]').focused, true);
  press('0', {ctrlKey: true});
  assert.equal(element('lab-zoom').value, 'fit');
  press('+', {ctrlKey: true, shiftKey: true});
  assert.equal(element('lab-zoom').value, '1.5');
  press('[', {ctrlKey: true});
  assert.equal(element('[data-order="-1"]').clicks, 1);
  press('o', {ctrlKey: true});
  assert.equal(dialog.open, true);
  press('s', {ctrlKey: true});
  assert.equal(element('lab-render').clicks, 1);
  dialog.close();
  element('lab-channel').disabled = true;
  press('s', {ctrlKey: true});
  assert.equal(element('lab-render').clicks, 1);
  element('lab-channel').disabled = false;
  press('h');
  const viewport = element('lab-viewport');
  viewport.listeners.pointerdown({button: 0, clientX: 30, clientY: 40, preventDefault() {}, stopPropagation() {}});
  viewport.listeners.pointermove({clientX: 50, clientY: 70});
  assert.equal(viewport.scrollLeft, 80);
  assert.equal(viewport.scrollTop, 70);
  viewport.listeners.pointerup();
  press('v');
  assert.equal(viewport.style.cursor, '');
  press(' ');
  windowHandlers.blur();
  assert.equal(viewport.style.cursor, '');
  assert.equal(press('s', {ctrlKey: true, altKey: true}).prevented, undefined);
});
