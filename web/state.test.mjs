import test from "node:test";
import assert from "node:assert/strict";
import { derivePrediction, normalizeState } from "./state.mjs";

const sample = {
  label: "Plastic bottle",
  confidence: 0.94,
  bin: "RECYCLE",
  arm_state: "READY",
  connected: true,
  counts: { RECYCLE: 7, TRASH: 3 },
  prediction_at: "2026-11-07T05:30:00Z"
};

test('access defaults preserve older local bridges and honor public viewer/operator state', () => {
  assert.equal(normalizeState(sample).public, false);
  assert.equal(normalizeState(sample).operator, true);
  assert.equal(normalizeState({ ...sample, public: true, operator: false }).public, true);
  assert.equal(normalizeState({ ...sample, public: true, operator: false }).operator, false);
  assert.equal(normalizeState({ ...sample, public: true, operator: true }).operator, true);
  for (const invalid of [null, 'true', 1]) {
    assert.equal(normalizeState({ ...sample, operator: invalid }).operator, false);
  }
});

test('manual bins default for older bridges and preserve a configured subset and order', () => {
  assert.deepEqual(normalizeState(sample).bins, ['RECYCLE', 'TRASH']);
  assert.deepEqual(normalizeState({ ...sample, bins: ['TRASH', 'RECYCLE'] }).bins, ['TRASH', 'RECYCLE']);
  assert.deepEqual(normalizeState({ ...sample, bins: [' paper ', 'RECYCLE', null, '', '<script>', 'GLASS'] }).bins,
    ['PAPER', 'RECYCLE', 'GLASS']);
  assert.deepEqual(normalizeState({ ...sample, bins: [] }).bins, []);
  assert.deepEqual(normalizeState({ ...sample, bins: null }).bins, ['RECYCLE', 'TRASH']);
});

test("the Swarms check is kept for the camera panel", () => {
  const held = normalizeState({ ...sample, bin: null, swarm_check: "Swarms held this item because it can leak" });
  assert.equal(held.swarmCheck, "Swarms held this item because it can leak");
  const approved = normalizeState({
    ...sample,
    swarm_check: "Swarms approves this item because it is a dry plastic snack bag."
  });
  assert.equal(approved.swarmCheck, "Swarms approves this item because it is a dry plastic snack bag.");
  assert.equal(normalizeState(sample).swarmCheck, "");
});

test("normalizes the agreed API fields without changing confirmed counts", () => {
  const state = normalizeState(sample);
  assert.equal(state.label, "Plastic bottle");
  assert.equal(state.confidence, 0.94);
  assert.equal(state.bin, "RECYCLE");
  assert.equal(state.armState, "READY");
  assert.deepEqual(state.counts, { RECYCLE: 7, TRASH: 3 });
});

test("normalizes camera source and vision availability for settings", () => {
  const state = normalizeState({ ...sample, camera_source: "esp32",
    camera_url: "http://192.168.1.50/capture", mock_available: true,
    mock_camera: false, vision_ready: false });
  assert.equal(state.cameraSource, "esp32");
  assert.equal(state.cameraUrl, "http://192.168.1.50/capture");
  assert.equal(state.mockAvailable, true);
  assert.equal(state.visionReady, false);
  assert.equal(normalizeState({ ...sample, mock_camera: true }).cameraSource, "mock");
  assert.equal(normalizeState({ ...sample, camera_error: "  Camera timed out.  " }).cameraError,
    "Camera timed out.");
});

test("unknown items have no displayed bin even if an invalid API response includes one", () => {
  const state = normalizeState({ ...sample, label: "UNKNOWN", bin: "TRASH" });
  assert.equal(state.bin, null);
  assert.equal(derivePrediction(state, { backendOnline: true, now: state.predictionAt }).kind, "review");
});

test("a named item stays on screen after its capture time ages", () => {
  const state = normalizeState(sample);
  const display = derivePrediction(state, { backendOnline: true, now: state.predictionAt + 9000 });
  assert.equal(display.kind, "detected");
  assert.equal(display.label, "Plastic bottle");
  assert.equal(display.bin, "RECYCLE");
});

test("an old unknown prediction is hidden instead of being shown as current", () => {
  const state = normalizeState({ ...sample, label: "UNKNOWN", bin: null });
  const display = derivePrediction(state, { backendOnline: true, now: state.predictionAt + 9000 });
  assert.equal(display.kind, "stale");
  assert.equal(display.bin, null);
  assert.equal(display.confidence, null);
});

test("a prediction without capture time is labeled as latest response", () => {
  const state = normalizeState({ ...sample, prediction_at: undefined });
  assert.equal(derivePrediction(state, { backendOnline: true }).kind, "latest");
});

test("API loss clears the displayed prediction", () => {
  const state = normalizeState(sample);
  const display = derivePrediction(state, { backendOnline: false });
  assert.equal(display.kind, "offline");
  assert.equal(display.bin, null);
});

test("camera loss hides the route even if the backend still reports an old item", () => {
  const state = normalizeState(sample);
  const display = derivePrediction(state, {
    backendOnline: true,
    cameraOnline: false,
    now: state.predictionAt
  });
  assert.equal(display.kind, "camera-offline");
  assert.equal(display.bin, null);
});

test("malformed state payloads fail and incomplete counts stay unavailable", () => {
  assert.throws(() => normalizeState(null), TypeError);
  assert.throws(() => normalizeState([]), TypeError);
  const state = normalizeState({ ...sample, counts: { RECYCLE: -1, TRASH: 0 } });
  assert.deepEqual(state.counts, { RECYCLE: null, TRASH: 0 });
});

test("accepts recycle and trash and rejects the four material routes", () => {
  for (const bin of ["RECYCLE", "TRASH"]) {
    assert.equal(normalizeState({ ...sample, bin: bin.toLowerCase() }).bin, bin);
  }
  for (const bin of ["PAPER", "PLASTIC", "METAL", "ORGANIC"]) {
    const state = normalizeState({ ...sample, bin });
    assert.equal(state.bin, null);
    assert.equal(derivePrediction(state, { backendOnline: true,
      cameraOnline: true, now: state.predictionAt }).kind, "review");
  }
  assert.deepEqual(normalizeState({ ...sample, counts: { PAPER: 9, PLASTIC: 6 } }).counts,
    { RECYCLE: null, TRASH: null });
});

test("a supported bin remains visible when the predictor omits confidence", () => {
  const state = normalizeState({ ...sample, confidence: null });
  const prediction = derivePrediction(state, {
    backendOnline: true, cameraOnline: true, now: state.predictionAt
  });
  assert.equal(prediction.bin, "RECYCLE");
  assert.equal(prediction.confidence, null);
  assert.match(prediction.note, /No model confidence score/);

  const untimed = normalizeState({ ...sample, confidence: null, prediction_at: undefined });
  const latest = derivePrediction(untimed, { backendOnline: true, cameraOnline: true });
  assert.equal(latest.kind, "latest");
  assert.equal(latest.bin, "RECYCLE");
  assert.match(latest.note, /Capture time and model confidence score were not supplied/);
});

test("completed-sort history is separate from the current prediction", () => {
  assert.equal(normalizeState(sample).recentSorts, null);
  assert.deepEqual(normalizeState({ ...sample, recent_sorts: [] }).recentSorts, []);
  const completed = { id: "sort-1", label: "Plastic bottle", bin: "RECYCLE",
    completed_at: "2026-11-07T05:31:00Z" };
  const state = normalizeState({ ...sample, label: "Paper flyer", bin: "RECYCLE",
    recent_sorts: [completed] });
  assert.equal(state.label, "Paper flyer");
  assert.deepEqual(state.recentSorts, [{ id: "sort-1", label: "Plastic bottle", bin: "RECYCLE",
    completedAt: "2026-11-07T05:31:00.000Z" }]);
});

test("history rejects invalid and duplicate events and keeps the newest 20", () => {
  const entries = Array.from({ length: 25 }, (_, index) => ({
    id: `sort-${index}`, label: `Item ${index}`, bin: "TRASH",
    completed_at: new Date(Date.UTC(2026, 10, 7, 5, index)).toISOString()
  }));
  entries.push(entries[24], { id: "bad-bin", label: "Item", bin: "COMPOST",
    completed_at: "2026-11-07T05:40:00Z" },
  { id: "legacy", label: "Item", bin: "PAPER", completed_at: "2026-11-07T05:40:00Z" },
  { id: "bad-date", label: "Item", bin: "TRASH", completed_at: "bad" },
  { id: "unknown", label: "UNKNOWN", bin: "RECYCLE", completed_at: "2026-11-07T05:40:00Z" });
  const history = normalizeState({ ...sample, recent_sorts: entries }).recentSorts;
  assert.equal(history.length, 20);
  assert.equal(history[0].id, "sort-24");
  assert.equal(history.at(-1).id, "sort-5");
  assert.equal(new Set(history.map(row => row.id)).size, 20);
});

test("a supported classifier decision can be displayed without fabricating confidence", () => {
  const state = normalizeState({ ...sample, confidence: null });
  const display = derivePrediction(state, { backendOnline: true, now: state.predictionAt });
  assert.equal(display.bin, "RECYCLE");
  assert.equal(display.confidence, null);
});

test('completed sort waits for clear instead of claiming identification is pending', () => {
  const state = normalizeState({label:'UNKNOWN', awaiting_clear:true, sorting_enabled:true, simulation:true,
    recent_sorts:[{id:'one',label:'Plastic bottle',bin:'RECYCLE',completed_at:new Date().toISOString()}]});
  const result = derivePrediction(state, {backendOnline:true, cameraOnline:true});
  assert.equal(result.kind, 'completed');
  assert.equal(result.label, 'Checking for a new item…');
  assert.match(result.note, /different item/);
  assert.equal(result.bin, null);
});

test('a sorted item name stays after its capture time ages', () => {
  const state = normalizeState({label:'Plastic bottle', bin:'RECYCLE',
    prediction_at:new Date(Date.now()-20000).toISOString(), simulation:true,
    awaiting_clear:true, sorting_enabled:true});
  const result = derivePrediction(state, {backendOnline:true, cameraOnline:true});
  assert.equal(result.label, 'Plastic bottle');
  assert.equal(result.bin, 'RECYCLE');
});

test('fresh plastic result replaces old metal label while duplicate-sort protection is active', () => {
  const now = Date.now();
  const state = normalizeState({label:'Plastic bottle', bin:'RECYCLE', prediction_at:new Date(now).toISOString(), simulation:true,
    awaiting_clear:true, sorting_enabled:true,
    recent_sorts:[{id:'can',label:'Metal can',bin:'TRASH',completed_at:new Date(now-1000).toISOString()}]});
  const result = derivePrediction(state, {backendOnline:true,cameraOnline:true,now});
  assert.equal(result.label,'Plastic bottle');
  assert.equal(result.bin,'RECYCLE');
  assert.match(result.note,/Show a different item/);
});

test('physical sorting still asks for a clear pickup area', () => {
  const now = Date.now();
  const state = normalizeState({label:'Metal can', bin:'TRASH', prediction_at:new Date(now).toISOString(),
    awaiting_clear:true, sorting_enabled:true, simulation:false});
  const result = derivePrediction(state, {backendOnline:true,cameraOnline:true,now});
  assert.match(result.note,/empty pickup area/);
});

test('temporary recognition outage shows automatic retry status', () => {
  const state = normalizeState({label:'UNKNOWN', sorting_enabled:true, vision_ready:true,
    status:'Gemini is temporarily unavailable (HTTP 503). Retrying automatically.'});
  const result = derivePrediction(state, {backendOnline:true,cameraOnline:true});
  assert.match(result.note,/Retrying automatically/);
});
