const ARM_STATES = new Set(["READY", "BUSY", "DONE", "ERROR"]);
const BINS = new Set(["RECYCLE", "TRASH"]);

function nonnegativeCount(value) {
  return Number.isInteger(value) && value >= 0 ? value : null;
}

function predictionTime(value) {
  if (typeof value !== "string" || value.trim() === "") return null;
  const parsed = Date.parse(value);
  return Number.isFinite(parsed) ? parsed : null;
}

export function normalizeState(raw) {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) {
    throw new TypeError("The state response must be a JSON object.");
  }

  const label = typeof raw.label === "string" && raw.label.trim()
    ? raw.label.trim().slice(0, 80)
    : "UNKNOWN";
  const confidence = typeof raw.confidence === "number" &&
    Number.isFinite(raw.confidence) && raw.confidence >= 0 && raw.confidence <= 1
    ? raw.confidence
    : null;
  const requestedBin = typeof raw.bin === "string" ? raw.bin.toUpperCase() : "";
  const bin = label.toUpperCase() !== "UNKNOWN" && BINS.has(requestedBin)
    ? requestedBin
    : null;
  const requestedArmState = typeof raw.arm_state === "string" ? raw.arm_state.toUpperCase() : "";
  const armState = ARM_STATES.has(requestedArmState) ? requestedArmState : "UNKNOWN";
  const rawCounts = raw.counts && typeof raw.counts === "object" && !Array.isArray(raw.counts)
    ? raw.counts
    : {};

  return {
    label,
    confidence,
    bin,
    armState,
    connected: raw.connected === true,
    counts: {
      RECYCLE: nonnegativeCount(rawCounts.RECYCLE ?? rawCounts.recycle),
      TRASH: nonnegativeCount(rawCounts.TRASH ?? rawCounts.trash)
    },
    predictionAt: predictionTime(raw.prediction_at ?? raw.timestamp)
  };
}

export function derivePrediction(state, options = {}) {
  const backendOnline = options.backendOnline === true;
  const now = Number.isFinite(options.now) ? options.now : Date.now();
  const staleAfterMs = Number.isFinite(options.staleAfterMs) ? options.staleAfterMs : 8000;

  if (!backendOnline) {
    return { kind: "offline", label: "No current item", confidence: null, bin: null,
      note: "Dashboard API unavailable. Predictions are hidden until it reconnects." };
  }
  if (options.cameraOnline === false) {
    return { kind: "camera-offline", label: "No current item", confidence: null, bin: null,
      note: "Camera frame unavailable. Predictions are hidden until it returns." };
  }
  if (!state) {
    return { kind: "empty", label: "No current item", confidence: null, bin: null,
      note: "Waiting for the first prediction." };
  }
  if (state.predictionAt !== null && now - state.predictionAt > staleAfterMs) {
    return { kind: "stale", label: "No current item", confidence: null, bin: null,
      note: "The last prediction is old. Waiting for a new camera result." };
  }
  if (state.label.toUpperCase() === "UNKNOWN" || state.bin === null || state.confidence === null) {
    return { kind: "review", label: state.label === "UNKNOWN" ? "Unknown item" : state.label,
      confidence: state.confidence, bin: null,
      note: state.predictionAt === null
        ? "No supported bin decision. Capture time was not supplied."
        : "No supported bin decision. The arm should remain still." };
  }
  return {
    kind: state.predictionAt === null ? "latest" : "detected",
    label: state.label,
    confidence: state.confidence,
    bin: state.bin,
    note: state.predictionAt === null
      ? "Latest API response. Capture time was not supplied."
      : "Recent prediction from the pickup camera."
  };
}

export function formatCount(value) {
  return value === null ? "—" : new Intl.NumberFormat().format(value);
}
