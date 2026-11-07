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
