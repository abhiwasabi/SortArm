import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import vm from 'node:vm';
import { derivePrediction, formatCount, normalizeState } from './state.mjs';

const source = (await readFile(new URL('./app.mjs', import.meta.url), 'utf8'))
  .replace(/^import .*;\n/, '');
const sample = { label: 'bottle', confidence: .94, bin: 'RECYCLE',
  connected: true, arm_state: 'BUSY', counts: { RECYCLE: 4, TRASH: 2 } };

function harness() {
  const elements = new Map();
  const requests = [];
  const timers = new Map();
  let now = 100000;
  let timerId = 0;
  class Clock extends Date { static now() { return now; } }
  const element = id => {
    if (!elements.has(id)) elements.set(id, {
      dataset: {}, style: {}, listeners: {}, src: '', writes: 0,
      setAttribute() {}, querySelector() { return this; },
      addEventListener(name, fn) { this.listeners[name] = fn; }
    });
    return elements.get(id);
  };
  Object.defineProperty(element('cameraFrame'), 'src', {
    get() { return this.url; }, set(value) { this.url = value; this.writes++; }
  });
  const context = vm.createContext({
    derivePrediction, formatCount, normalizeState, Date: Clock,
    URLSearchParams, AbortController,
    window: { location: { search: '' } },
    document: { getElementById: element },
    setTimeout(fn, ms) { timers.set(++timerId, { fn, ms }); return timerId; },
    clearTimeout(id) { timers.delete(id); },
    fetch(url) { return new Promise(resolve => requests.push({ url, resolve })); }
  });
  vm.runInContext(source, context);
  return {
    element, requests, timers,
    advance(ms) { now += ms; },
    run(code) { return vm.runInContext(code, context); },
    async respond(index, state = sample) {
      requests[index].resolve({ ok: true, json: async () => state });
      await new Promise(resolve => setImmediate(resolve));
    }
  };
}

test('slow frames survive state polls and retry after the frame deadline', async () => {
  const h = harness();
  await h.respond(0);
  assert.equal(h.element('cameraFrame').writes, 1);
  h.advance(1200);
  const poll = h.run('pollState()');
  await h.respond(1);
  await poll;
  assert.equal(h.element('cameraFrame').writes, 1);
  h.advance(4500);
  const retry = h.run('pollState()');
  await h.respond(2);
  await retry;
  assert.equal(h.element('cameraFrame').writes, 2);
  h.element('cameraFrame').listeners.load();
  assert.equal(h.element('cameraStatus').textContent, 'Connected');
});

test('STOP ignores pre-POST state requests and unrecognized controller states', async () => {
  const h = harness();
  await h.respond(0);
  const oldPoll = h.run('pollState()');
  const stop = h.run('requestStop()');
  await h.respond(2);
  await stop;
  await h.respond(1, { ...sample, arm_state: 'READY' });
  await oldPoll;
  assert.match(h.element('notice').textContent, /Waiting for the arm controller/);
  const unknownPoll = h.run('pollState()');
  await h.respond(3, { ...sample, arm_state: 'INVALID' });
  await unknownPoll;
  assert.match(h.element('notice').textContent, /Waiting for the arm controller/);
  const readyPoll = h.run('pollState()');
  await h.respond(4, { ...sample, arm_state: 'READY' });
  await readyPoll;
  assert.match(h.element('notice').textContent, /Controller now reports READY/);
});

test('STOP warning deadline runs even while a state request is stalled', async () => {
  const h = harness();
  await h.respond(0);
  const stop = h.run('requestStop()');
  await h.respond(1);
  await stop;
  h.run('pollState()');
  const deadline = [...h.timers.values()].find(timer => timer.ms === 5000);
  deadline.fn();
  assert.match(h.element('notice').textContent, /not confirmed after five seconds/);
});
