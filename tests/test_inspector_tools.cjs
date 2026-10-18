const assert = require('node:assert/strict');
const {test} = require('node:test');
const {readFileSync} = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

test('inspector ratio, six alignments and color input guards', () => {
  const source = readFileSync(path.join(__dirname, '../src/cardnews_auto/web/static/template-studio.js'), 'utf8');
  const context = {values: () => ({x: 20, y: 30, width: 100, height: 50}),
    bounds: {}, selected: 'layer', doc: {size: [1000, 800]}, busy: false,
    remember() { this.remembered = true; }, Event: class {},
  };
  vm.createContext(context);
  vm.runInContext(source.slice(source.indexOf('  function resizeDimensions'), source.indexOf('  function properties()')), context);
  assert.equal(context.resizeDimensions({width: 200, height: 100}, 'width', 400, true).height, 200);
  assert.equal(context.resizeDimensions({width: 200, height: 100}, 'height', 400, true).width, 800);
  assert.equal(context.resizeDimensions({width: 100, height: 4000}, 'width', 200, true), null);
  assert.equal(context.resizeDimensions({width: 200, height: 100}, 'width', 400, false).height, undefined);
  for (const [direction, axis, expected] of [['left','x',0],['center','x',450],['right','x',900],['top','y',0],['middle','y',375],['bottom','y',750]]) {
    assert.equal(context.alignLayer(direction)[axis], expected);
  }
  const picker = {value: '#000000', dispatchEvent() { this.sent = true; }};
  const input = {value: '#12abff', dataset: {}, checkValidity: () => true,
    closest: () => ({querySelector: () => picker})};
  context.editColor(input);
  assert.equal(picker.value, '#12abff');
  input.value = '#oops';
  context.editColor(input);
  assert.equal(picker.value, '#12abff');
  context.busy = true;
  context.editColor(input, '#ffffff');
  assert.equal(picker.value, '#12abff');
});
