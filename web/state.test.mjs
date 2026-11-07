import test from "node:test";
import assert from "node:assert/strict";
import { derivePrediction, normalizeState } from "./state.mjs";

const sample = {
  label: "bottle",
  confidence: 0.94,
  bin: "RECYCLE",
  arm_state: "READY",
  connected: true,
  counts: { RECYCLE: 4, TRASH: 2 },
  prediction_at: "2026-11-07T05:30:00Z"
};

test("normalizes the agreed API fields without changing confirmed counts", () => {
  const state = normalizeState(sample);
  assert.equal(state.label, "bottle");
  assert.equal(state.confidence, 0.94);
  assert.equal(state.bin, "RECYCLE");
  assert.equal(state.armState, "READY");
  assert.deepEqual(state.counts, { RECYCLE: 4, TRASH: 2 });
});

test("unknown items have no displayed bin even if an invalid API response includes one", () => {
  const state = normalizeState({ ...sample, label: "UNKNOWN", bin: "TRASH" });
  assert.equal(state.bin, null);
  assert.equal(derivePrediction(state, { backendOnline: true, now: state.predictionAt }).kind, "review");
});

test("an old prediction is hidden instead of being shown as current", () => {
  const state = normalizeState(sample);
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
  const state = normalizeState({ ...sample, counts: { RECYCLE: -1 } });
  assert.deepEqual(state.counts, { RECYCLE: null, TRASH: null });
});

test("completed-sort history is separate from the current prediction", () => {
  assert.equal(normalizeState(sample).recentSorts, null);
  assert.deepEqual(normalizeState({ ...sample, recent_sorts: [] }).recentSorts, []);
  const completed = { id: "sort-1", label: "Bottle", bin: "RECYCLE",
    completed_at: "2026-11-07T05:31:00Z" };
  const state = normalizeState({ ...sample, label: "Wrapper", bin: "TRASH",
    recent_sorts: [completed] });
  assert.equal(state.label, "Wrapper");
  assert.deepEqual(state.recentSorts, [{ id: "sort-1", label: "Bottle", bin: "RECYCLE",
    completedAt: "2026-11-07T05:31:00.000Z" }]);
});

test("history rejects invalid and duplicate events and keeps the newest 20", () => {
  const entries = Array.from({ length: 25 }, (_, index) => ({
    id: `sort-${index}`, label: `Item ${index}`, bin: "RECYCLE",
    completed_at: new Date(Date.UTC(2026, 10, 7, 5, index)).toISOString()
  }));
  entries.push(entries[24], { id: "bad-bin", label: "Item", bin: "COMPOST",
    completed_at: "2026-11-07T05:40:00Z" },
  { id: "bad-date", label: "Item", bin: "TRASH", completed_at: "bad" },
  { id: "unknown", label: "UNKNOWN", bin: "TRASH", completed_at: "2026-11-07T05:40:00Z" });
  const history = normalizeState({ ...sample, recent_sorts: entries }).recentSorts;
  assert.equal(history.length, 20);
  assert.equal(history[0].id, "sort-24");
  assert.equal(history.at(-1).id, "sort-5");
  assert.equal(new Set(history.map(row => row.id)).size, 20);
});
