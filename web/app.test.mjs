import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import vm from 'node:vm';
import { BIN_KEYS, derivePrediction, formatCount, normalizeState } from './state.mjs';

const source = (await readFile(new URL('./app.mjs', import.meta.url), 'utf8'))
  .replace(/^import .*;\r?\n/, '');
const sample = { label: 'Plastic bottle', confidence: .94, bin: 'PLASTIC',
  connected: true, arm_state: 'BUSY',
  counts: { PAPER: 3, PLASTIC: 4, METAL: 2, ORGANIC: 1 } };

function harness(search = '') {
  const elements = new Map();
  const requests = [];
  const timers = new Map();
  let now = 100000;
  let timerId = 0;
  class Clock extends Date { static now() { return now; } }
  const makeElement = () => ({
    dataset: {}, style: {}, listeners: {}, src: '', value: '', writes: 0, children: [],
    setAttribute() {}, querySelector() { return this; },
    removeAttribute(name) { if (name === 'src') this.src = ''; },
    addEventListener(name, fn) { this.listeners[name] = fn; },
    append(...nodes) { this.children.push(...nodes); },
    replaceChildren(...nodes) { this.children = nodes; }
  });
  const element = id => {
    if (!elements.has(id)) elements.set(id, makeElement());
    return elements.get(id);
  };
  Object.defineProperty(element('cameraFrame'), 'src', {
    get() { return this.url; }, set(value) { this.url = value; this.writes++; }
  });
  const context = vm.createContext({
    BIN_KEYS, derivePrediction, formatCount, normalizeState, Date: Clock,
    URL, URLSearchParams, AbortController,
    window: { location: { search } },
    document: { baseURI: 'http://127.0.0.1:4173/', getElementById: element, createElement: makeElement },
    setTimeout(fn, ms) { timers.set(++timerId, { fn, ms }); return timerId; },
    clearTimeout(id) { timers.delete(id); },
    fetch(url, options) { return new Promise(resolve => requests.push({ url, options, resolve })); }
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
  h.advance(6500);
  const retry = h.run('pollState()');
  await h.respond(2);
  await retry;
  assert.equal(h.element('cameraFrame').writes, 2);
  h.element('cameraFrame').listeners.load();
  assert.equal(h.element('cameraStatus').textContent, 'Connected');
});

test('camera frames refresh between state polls without overlapping image requests', async () => {
  const h = harness();
  await h.respond(0, { ...sample, camera_source: 'esp32' });
  const frame = h.element('cameraFrame');
  assert.equal(frame.writes, 1);
  frame.listeners.load();
  const frameTimer = [...h.timers.values()].find(timer => timer.ms === 100);
  assert.ok(frameTimer);

  h.advance(300);
  frameTimer.fn();
  assert.equal(frame.writes, 2);
  h.advance(300);
  frameTimer.fn();
  assert.equal(frame.writes, 2);
  frame.listeners.load();
  h.advance(300);
  frameTimer.fn();
  assert.equal(frame.writes, 3);
  assert.equal(h.requests.length, 1);
});

test('STOP ignores pre-POST requests and needs ERROR, not READY or DONE, for confirmation', async () => {
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
  assert.match(h.element('notice').textContent, /Waiting for the arm controller/);
  const donePoll = h.run('pollState()');
  await h.respond(5, { ...sample, arm_state: 'DONE' });
  await donePoll;
  assert.match(h.element('notice').textContent, /Waiting for the arm controller/);
  const stoppedPoll = h.run('pollState()');
  await h.respond(6, { ...sample, arm_state: 'ERROR', arm_detail: 'stopped' });
  await stoppedPoll;
  assert.equal(h.element('notice').textContent,
    'Arm stopped. Check it is clear, then press Home arm to recover.');
});

test('STOP on an unresponsive arm keeps the power-cutoff warning instead of reporting success', async () => {
  const h = harness();
  await h.respond(0);
  const stop = h.run('requestStop()');
  await h.respond(1);
  await stop;
  const poll = h.run('pollState()');
  await h.respond(2, { ...sample, connected: true, arm_state: 'ERROR', arm_detail: 'stopped; arm not responding' });
  await poll;
  const notice = h.element('notice').textContent;
  assert.match(notice, /not responding/);
  assert.match(notice, /physical power cutoff/);
  assert.doesNotMatch(notice, /Arm stopped/);
});

test('manual sort is enabled only with an online, connected, ready, paused bridge and no pending control', async () => {
  const ready = { ...sample, arm_state: 'READY', sorting_enabled: false,
    vision_ready: false, camera_source: 'off' };
  const scenarios = [
    { name: 'ready without camera or vision', state: ready, enabled: true },
    { name: 'API offline', state: ready, setup: 'backendOnline = false', enabled: false },
    { name: 'no bridge', state: { ...ready, sorting_enabled: undefined }, enabled: false },
    { name: 'disconnected', state: { ...ready, connected: false }, enabled: false },
    { name: 'busy', state: { ...ready, arm_state: 'BUSY' }, enabled: false },
    { name: 'done', state: { ...ready, arm_state: 'DONE' }, enabled: false },
    { name: 'error', state: { ...ready, arm_state: 'ERROR' }, enabled: false },
    { name: 'unknown arm state', state: { ...ready, arm_state: 'invalid' }, enabled: false },
    { name: 'automatic sorting', state: { ...ready, sorting_enabled: true }, enabled: false },
    { name: 'control command pending', state: ready, setup: 'controlPending = true', enabled: false },
    { name: 'STOP POST pending', state: ready, setup: 'stopPending = true', enabled: false },
    { name: 'STOP confirmation pending', state: ready, setup: 'awaitingStopState = true', enabled: false }
  ];
  for (const scenario of scenarios) {
    const h = harness();
    await h.respond(0, scenario.state);
    if (scenario.setup) h.run(scenario.setup + '; render()');
    const buttons = h.element('manualSortButtons').children;
    assert.equal(buttons.length, 4, scenario.name);
    assert.equal(buttons.every(button => !button.disabled), scenario.enabled, scenario.name);
    if (!scenario.enabled) {
      await h.run('bridgeCommand("/api/sort", { bin: "PAPER" })');
      assert.equal(h.requests.length, 1, scenario.name + ' must not send a command');
    }
  }
});

test('manual buttons follow state bins and post through the shared helper without changing counts', async () => {
  const h = harness();
  const ready = { ...sample, arm_state: 'READY', sorting_enabled: false,
    camera_source: 'off', vision_ready: false, bins: ['METAL', 'PAPER'] };
  await h.respond(0, ready);
  const buttons = h.element('manualSortButtons').children;
  assert.deepEqual(buttons.map(button => button.dataset.bin), ['METAL', 'PAPER']);
  assert.equal(h.element('manualSortCard').hidden, false);
  const command = buttons[1].listeners.click();
  assert.equal(h.requests[1].url, '/api/sort');
  assert.equal(h.requests[1].options.method, 'POST');
  assert.deepEqual(JSON.parse(h.requests[1].options.body), { bin: 'PAPER' });
  assert.ok(buttons.every(button => button.disabled));
  assert.equal(h.element('homeButton').disabled, true);
  assert.equal(h.element('sortingButton').disabled, true);
  await h.respond(1, { accepted: true, state: { ...ready, arm_state: 'BUSY', arm_detail: 'SORT PAPER' } });
  await command;
  assert.equal(h.element('paperCount').textContent, String(sample.counts.PAPER));
  assert.match(h.element('notice').textContent, /Manual sort requested for Paper bin/);
  assert.equal(h.element('armDescription').textContent, 'Moving: SORT PAPER');
  assert.equal(h.element('manualSortButtons').children[0], buttons[0]);
});

test('home, manual sort, and automatic sorting show the 409 response error and state', async () => {
  for (const [path, body] of [['/api/home', {}], ['/api/sort', { bin: 'PAPER' }],
    ['/api/sorting', { enabled: true }]]) {
    const h = harness();
    const ready = { ...sample, arm_state: 'READY', sorting_enabled: false };
    await h.respond(0, ready);
    const command = h.run(`bridgeCommand(${JSON.stringify(path)}, ${JSON.stringify(body)})`);
    const error = path === '/api/sorting' ? 'Pause automatic sorting before a manual sort.' : 'Calibration has not been verified.';
    h.requests[1].resolve({ ok: false, status: 409, json: async () => ({ accepted: false, error,
      state: { ...ready, arm_state: 'ERROR', arm_detail: 'Calibration required.' } }) });
    await command;
    assert.equal(h.element('notice').textContent, error, path);
    assert.equal(h.element('notice').dataset.tone, 'error', path);
    assert.equal(h.element('armDescription').textContent, 'Calibration required. Press Home arm to recover.');
  }
});

test('STOP uses an eight-second request timeout and blocks motion through confirmation', async () => {
  const h = harness();
  const ready = { ...sample, arm_state: 'READY', sorting_enabled: false };
  await h.respond(0, ready);
  h.element('cameraFrame').listeners.load();
  const stop = h.run('requestStop()');
  assert.ok([...h.timers.values()].some(timer => timer.ms === 8000));
  for (const id of ['homeButton', 'sortingButton']) assert.equal(h.element(id).disabled, true);
  assert.ok(h.element('manualSortButtons').children.every(button => button.disabled));
  await h.respond(1);
  await stop;
  for (const id of ['homeButton', 'sortingButton']) assert.equal(h.element(id).disabled, true);
  for (const command of ['bridgeCommand("/api/home")',
    'bridgeCommand("/api/sorting", { enabled: true })',
    'bridgeCommand("/api/sort", { bin: "PAPER" })']) await h.run(command);
  assert.equal(h.requests.length, 2);
  const poll = h.run('pollState()');
  await h.respond(2, { ...ready, arm_state: 'ERROR', arm_detail: 'STOP requested' });
  await poll;
  assert.equal(h.element('homeButton').disabled, false);
  assert.match(h.element('notice').textContent, /then press Home arm to recover/);
});

test('arm details remain visible while disconnected, moving, and in error', async () => {
  for (const [state, expected] of [
    [{ connected: false, arm_state: 'ERROR', arm_detail: 'connection lost: bus timed out' }, 'connection lost: bus timed out'],
    [{ connected: true, arm_state: 'BUSY', arm_detail: 'SORT PLASTIC' }, 'Moving: SORT PLASTIC'],
    [{ connected: true, arm_state: 'ERROR', arm_detail: 'Joint limit exceeded.' }, 'Joint limit exceeded. Press Home arm to recover.'],
    [{ connected: true, arm_state: 'ERROR', arm_detail: 'stopped; arm not responding' },
      'stopped; arm not responding. Use the physical power cutoff if it is still moving.']
  ]) {
    const h = harness();
    await h.respond(0, { ...sample, ...state });
    assert.equal(h.element('armDescription').textContent, expected);
  }
});

test('sample preview hides the manual sort card and cannot dispatch a manual sort', async () => {
  const h = harness('?demo=1');
  assert.equal(h.element('bridgeControls').hidden, true);
  assert.equal(h.element('manualSortCard').hidden, true);
  assert.ok(h.element('manualSortButtons').children.every(button => button.disabled));
  await h.run('bridgeCommand("/api/sort", { bin: "PAPER" })');
  assert.equal(h.requests.length, 0);
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


test('bridge controls reflect homing, simulation, and paused sorting', async () => {
  const h = harness();
  await h.respond(0, { ...sample, arm_state: 'READY', simulation: true,
    mock_camera: true, sorting_enabled: false, status: 'Sorting paused.' });
  assert.equal(h.element('bridgeControls').hidden, false);
  assert.equal(h.element('runtimeMode').textContent, 'SIMULATED ARM');
  assert.equal(h.element('mockControls').hidden, false);
  assert.equal(h.element('sortingButton').textContent, 'Start sorting');
  const command = h.run('bridgeCommand("/api/home")');
  await h.respond(1, {});
  await command;
  assert.equal(h.requests[1].url, '/api/home');
});

test('ESP32 camera settings load from the API and disable sorting without vision', async () => {
  const h = harness();
  await h.respond(0, { ...sample, arm_state: 'READY', sorting_enabled: false,
    camera_source: 'esp32', camera_url: 'http://192.168.1.50/capture',
    mock_available: true, mock_camera: false, vision_ready: false });
  h.element('cameraFrame').listeners.load();
  assert.equal(h.element('cameraSourceCard').hidden, false);
  assert.equal(h.element('cameraSourceSelect').value, 'esp32');
  assert.equal(h.element('esp32CameraUrl').value, 'http://192.168.1.50/capture');
  assert.equal(h.element('esp32CameraFields').hidden, false);
  assert.equal(h.element('sortingButton').disabled, true);
  assert.match(h.element('cameraSourceStatus').textContent, /Preview only/);
});

test('an ESP32 capture error replaces a stale connected label with a useful reason', async () => {
  const h = harness();
  await h.respond(0, { ...sample, arm_state: 'READY', sorting_enabled: false,
    camera_source: 'esp32', camera_url: 'http://192.168.1.50/capture',
    mock_available: true, vision_ready: true });
  h.element('cameraFrame').listeners.load();
  assert.equal(h.element('cameraStatus').textContent, 'Connected');
  const poll = h.run('pollState()');
  await h.respond(1, { ...sample, arm_state: 'READY', sorting_enabled: false,
    camera_source: 'esp32', camera_url: 'http://192.168.1.50/capture',
    mock_available: true, vision_ready: true,
    camera_error: 'ESP32 did not return a JPEG frame.' });
  await poll;
  assert.equal(h.element('cameraStatus').textContent, 'Disconnected');
  assert.equal(h.element('cameraSourceStatus').textContent, 'ESP32 unavailable');
  assert.equal(h.element('cameraSourceError').textContent, 'ESP32 did not return a JPEG frame.');
  assert.equal(h.element('cameraSourceError').hidden, false);
  assert.equal(h.element('sortingButton').disabled, true);

  // A late load from the failed request cannot mark the old image as live.
  h.element('cameraFrame').listeners.load();
  assert.equal(h.element('cameraStatus').textContent, 'Disconnected');
  const recoveryPoll = h.run('pollState()');
  await h.respond(2, { ...sample, arm_state: 'READY', sorting_enabled: false,
    camera_source: 'esp32', camera_url: 'http://192.168.1.50/capture',
    mock_available: true, vision_ready: true });
  await recoveryPoll;
  assert.equal(h.element('cameraStatus').textContent, 'Disconnected');
  assert.equal(h.element('cameraFrame').hidden, true);
  h.element('cameraFrame').listeners.load();
  assert.equal(h.element('cameraStatus').textContent, 'Connected');
  assert.equal(h.element('cameraFrame').hidden, false);
});

test('a slower ESP32 frame remains connected during a normal capture cycle', async () => {
  const h = harness();
  await h.respond(0, { ...sample, camera_source: 'esp32' });
  h.element('cameraFrame').listeners.load();
  h.advance(5000);
  const poll = h.run('pollState()');
  await h.respond(1, { ...sample, camera_source: 'esp32' });
  await poll;
  assert.equal(h.element('cameraStatus').textContent, 'Connected');
  h.advance(5001);
  h.run('render()');
  assert.equal(h.element('cameraStatus').textContent, 'Disconnected');
});

test('camera selection preserves an unfinished URL and posts the chosen source', async () => {
  const h = harness();
  const mockState = { ...sample, arm_state: 'READY', sorting_enabled: false,
    camera_source: 'mock', mock_camera: true, mock_available: true, vision_ready: true };
  await h.respond(0, mockState);
  const select = h.element('cameraSourceSelect');
  select.value = 'esp32';
  select.listeners.change();
  const url = h.element('esp32CameraUrl');
  url.value = 'http://192.168.1.50/capture';
  url.listeners.input();
  const poll = h.run('pollState()');
  await h.respond(1, mockState);
  await poll;
  assert.equal(select.value, 'esp32');
  assert.equal(url.value, 'http://192.168.1.50/capture');
  const command = h.run('applyCameraSource()');
  assert.equal(h.requests[2].url, '/api/camera');
  assert.equal(h.requests[2].options.method, 'POST');
  assert.deepEqual(JSON.parse(h.requests[2].options.body),
    { source: 'esp32', url: 'http://192.168.1.50/capture' });
  assert.equal(h.element('applyCameraButton').disabled, true);
  await h.respond(2, { accepted: true, state: { ...mockState,
    camera_source: 'esp32', camera_url: 'http://192.168.1.50/capture',
    mock_camera: false, vision_ready: false } });
  await command;
  assert.equal(h.element('cameraFrame').hidden, true);
  assert.equal(h.element('mockControls').hidden, true);
  assert.match(h.element('notice').textContent, /ESP32 camera selected/);
});

test('sample preview hides camera source controls and cannot change the server', async () => {
  const h = harness('?demo=1');
  assert.equal(h.element('cameraSourceCard').hidden, true);
  h.element('cameraSourceSelect').value = 'esp32';
  await h.run('applyCameraSource()');
  assert.equal(h.requests.length, 0);
});

test('renders four material totals and every route even without a confidence score', async () => {
  const h = harness();
  await h.respond(0, { ...sample, confidence: null });
  h.element('cameraFrame').listeners.load();
  for (const [bin, count] of Object.entries(sample.counts)) {
    assert.equal(h.element(bin.toLowerCase() + 'Count').textContent, String(count));
  }
  assert.equal(h.element('confidenceValue').textContent, '—');
  const labels = {
    PAPER: 'Paper bin', PLASTIC: 'Plastic bin',
    METAL: 'Metal bin', ORGANIC: 'Organic bin'
  };
  assert.equal(h.element('binDecision').textContent, labels.PLASTIC);
  for (const [index, bin] of ['PAPER', 'METAL', 'ORGANIC'].entries()) {
    const poll = h.run('pollState()');
    await h.respond(index + 1, { ...sample, bin, confidence: null });
    await poll;
    assert.equal(h.element('binDecision').textContent, labels[bin]);
    assert.equal(h.element('confidenceValue').textContent, '—');
  }
});

test('history shows confirmed sorts once across repeated polls and prediction changes', async () => {
  const h = harness();
  const completed = [
    { id: 'sort-4', label: 'Paper flyer', bin: 'PAPER', completed_at: '2026-11-07T05:34:00Z' },
    { id: 'sort-3', label: 'Plastic bottle', bin: 'PLASTIC', completed_at: '2026-11-07T05:33:00Z' },
    { id: 'sort-2', label: 'Metal can', bin: 'METAL', completed_at: '2026-11-07T05:32:00Z' },
    { id: 'sort-1', label: 'Banana peel', bin: 'ORGANIC', completed_at: '2026-11-07T05:31:00Z' }
  ];
  const state = { ...sample, recent_sorts: completed };
  await h.respond(0, state);
  const list = h.element('sortHistory');
  assert.equal(h.element('historyStatus').textContent, 'LIVE');
  assert.equal(list.children.length, 4);
  assert.deepEqual(list.children.map(row => row.children[2].textContent),
    ['Paper bin', 'Plastic bin', 'Metal bin', 'Organic bin']);
  assert.deepEqual(list.children.map(row => row.children[0].dataset.bin),
    ['PAPER', 'PLASTIC', 'METAL', 'ORGANIC']);
  const firstRow = list.children[0];

  const repeatedPoll = h.run('pollState()');
  await h.respond(1, { ...state, label: 'Banana peel', bin: 'ORGANIC' });
  await repeatedPoll;
  assert.equal(list.children.length, 4);
  assert.equal(list.children[0], firstRow);

  const next = { id: 'sort-5', label: 'Newspaper', bin: 'PAPER',
    completed_at: '2026-11-07T05:35:00Z' };
  const nextPoll = h.run('pollState()');
  await h.respond(2, { ...state, recent_sorts: [next, ...completed] });
  await nextPoll;
  assert.equal(list.children.length, 5);
  assert.equal(list.children[0].children[1].textContent, 'Newspaper');
  assert.match(h.element('historyAnnouncement').textContent, /Newspaper sorted into Paper bin/);
});

test('history distinguishes absent records from API loss', async () => {
  const h = harness();
  await h.respond(0);
  assert.equal(h.element('historyStatus').textContent, 'UNAVAILABLE');
  assert.match(h.element('historyMessage').textContent, /Waiting for completed-sort records/);
  const failedPoll = h.run('pollState()');
  h.requests[1].resolve({ ok: false, status: 503 });
  await failedPoll;
  assert.equal(h.element('historyStatus').textContent, 'OFFLINE');
  assert.match(h.element('historyMessage').textContent, /dashboard API is offline/);
});

test('Start camera cannot fall back to index zero when the selected iPhone is unavailable', async () => {
  const h = harness();
  await h.respond(0, {...sample, camera_source:'off', sorting_enabled:false});
  h.element('cameraSourceSelect').value='iphone';
  h.element('usbCameraDevice').value='';
  await h.element('toggleCameraButton').listeners.click();
  assert.equal(h.requests.length,1);
  assert.match(h.element('notice').textContent,/unavailable or still being detected/);
});
