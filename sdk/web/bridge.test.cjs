// SPDX-License-Identifier: Apache-2.0
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { createRequire } = require('node:module');
const { JSDOM } = createRequire(require('node:path').resolve(__dirname, '../../viewer/package.json'))('jsdom');
const source = readFileSync(__dirname + '/bridge.js', 'utf8');
function fixture() {
  const dom = new JSDOM('<body><button id="save">Save</button><input id="name" aria-label="Name"><input id="secret" type="password" value="private"><div id="list"><p>Row</p></div><canvas id="canvas"></canvas></body>', { url: 'https://app.invalid', runScripts: 'outside-only' });
  const w = dom.window;
  w.PointerEvent = w.MouseEvent;
  w.HTMLElement.prototype.getBoundingClientRect = function () { return { x: 10, y: 20, top: 20, left: 10, bottom: 60, right: 110, width: 100, height: 40 }; };
  w.HTMLElement.prototype.getClientRects = function () { return [this.getBoundingClientRect()]; };
  w.HTMLElement.prototype.scrollIntoView = function () {};
  w.HTMLElement.prototype.scrollBy = function (delta) { this.delta = delta; };
  let selected = w.document.getElementById('save');
  w.document.elementFromPoint = () => selected;
  w.eval(source);
  return { w, bridge: w.__SimMirrorWeb, choose: id => { selected = w.document.getElementById(id); }, close: () => dom.window.close() };
}
const options = { maxNodes: 3000, offset: { x: 0, y: 32 }, app: { bundle_id: 'dev.simmirror.Sample', name: 'Sample', pid: 42, active: false }, screen: { width_pt: 480, height_pt: 672, scale: 2, orientation: 'portrait' } };
test('capture keeps DOM identifiers and bounds and never password contents', () => {
  const f = fixture();
  try {
    const tree = f.bridge.capture(options);
    const nodes = tree.windows[0].nodes[0].children;
    assert.equal(nodes[0].identifier, 'save');
    assert.equal(nodes[0].frame.y, 52);
    assert.equal(nodes[2].kind, 'secure');
    assert.equal(nodes[2].value, null);
    assert(!JSON.stringify(tree).includes('private'));
    assert(f.bridge.capture({ ...options, maxNodes: 1 }).truncated);
    assert.match(tree.notes[0], /trusted native events are unavailable/);
    const hosted = f.bridge.capture({ ...options, notes: ['host note', 2, 3, 4, 5, 6, 7, 8, 9] }).notes;
    assert.deepEqual(hosted, ['host note', '2', '3', '4', '5', '6', '7', '8']);
  } finally { f.close(); }
});
test('button and canvas events stay in the document and controlled input receives native value and input events', () => {
  const f = fixture();
  try {
    let clicks = 0;
    f.w.document.getElementById('save').addEventListener('click', () => clicks++);
    f.bridge.input({ kind: 'tap', x: 20, y: 60 }, options.offset);
    assert.equal(clicks, 1);
    f.choose('name');
    const field = f.w.document.getElementById('name');
    let state = '';
    field.addEventListener('input', () => { state = field.value; });
    f.bridge.input({ kind: 'tap', x: 20, y: 60 }, options.offset);
    f.bridge.input({ kind: 'type', text: 'Example', clear: true }, options.offset);
    f.bridge.input({ kind: 'type', text: ' name' }, options.offset);
    assert.equal(state, 'Example name');
    assert.throws(() => f.bridge.input({ kind: 'type', text: 'wrong', submit: true }, options.offset), /not changed/);
    assert.equal(state, 'Example name');
    f.choose('canvas');
    let points = 0;
    f.w.document.getElementById('canvas').addEventListener('pointerdown', () => points++);
    f.bridge.input({ kind: 'tap', x: 20, y: 60 }, options.offset);
    assert.equal(points, 1);
  } finally { f.close(); }
});
test('invalid coordinates, missing fields, unsupported native actions and detached targets refuse', () => {
  const f = fixture();
  try {
    assert.throws(() => f.bridge.input({ kind: 'type', text: 'x' }, options.offset), /choose/);
    for (const x of [-1, Infinity, NaN, 100000]) assert.throws(() => f.bridge.input({ kind: 'tap', x, y: 60 }, options.offset), /outside/);
    assert.throws(() => f.bridge.input({ kind: 'menu' }, options.offset), /host hook/);
    assert.throws(() => f.bridge.input({ kind: 'keys', text: 'cmd+tab' }, options.offset), /host hook/);
    assert.throws(() => f.bridge.input({ kind: 'scroll', x: 20, y: 60, dx: NaN, dy: 1 }, options.offset), /delta/);
    f.choose('name');
    f.bridge.input({ kind: 'tap', x: 20, y: 60 }, options.offset);
    f.w.document.getElementById('name').remove();
    assert.throws(() => f.bridge.input({ kind: 'type', text: 'x' }, options.offset), /choose/);
  } finally { f.close(); }
});

test('keys move focus within the document and refuse unavailable browser defaults', () => {
  const f = fixture();
  try {
    const button = f.w.document.getElementById('save');
    let clicks = 0;
    button.addEventListener('click', () => clicks++);
    button.focus();
    f.bridge.input({ kind: 'keys', text: 'return' }, options.offset);
    assert.equal(clicks, 1);
    f.bridge.input({ kind: 'keys', text: 'tab' }, options.offset);
    assert.equal(f.w.document.activeElement.id, 'name');
    f.bridge.input({ kind: 'type', text: 'Example', clear: true }, options.offset);
    f.bridge.input({ kind: 'keys', text: 'left' }, options.offset);
    assert.equal(f.w.document.activeElement.selectionStart, 6);
    f.bridge.input({ kind: 'keys', text: 'right' }, options.offset);
    assert.equal(f.w.document.activeElement.selectionStart, 7);
    f.bridge.input({ kind: 'keys', text: 'shift+tab' }, options.offset);
    assert.equal(f.w.document.activeElement, button);
    assert.throws(() => f.bridge.input({ kind: 'keys', text: 'escape' }, options.offset), /default is unavailable/);
    button.addEventListener('keydown', event => event.preventDefault());
    f.bridge.input({ kind: 'keys', text: 'escape' }, options.offset);
  } finally { f.close(); }
});

test('capture clips list rows to their scrolling ancestor', () => {
  const f = fixture();
  try {
    const list = f.w.document.getElementById('list');
    list.style.overflowY = 'auto';
    list.firstElementChild.getBoundingClientRect = () => ({ x: 10, y: 200, left: 10, top: 200, right: 100, bottom: 220, width: 90, height: 20 });
    const nodes = f.bridge.capture(options).windows[0].nodes[0].children;
    assert.equal(nodes.find(n => n.identifier === 'list').children.length, 0);
    assert.throws(() => f.bridge.input({ kind: 'right_click', x: 20, y: 60 }, options.offset), /without an app handler/);
    f.w.document.getElementById('save').addEventListener('contextmenu', e => e.preventDefault());
    f.bridge.input({ kind: 'right_click', x: 20, y: 60 }, options.offset);
  } finally { f.close(); }
});
