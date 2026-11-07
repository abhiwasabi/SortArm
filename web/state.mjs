const ARM_STATES = new Set(["READY", "BUSY", "DONE", "ERROR"]);
export const BIN_KEYS = ["PAPER", "PLASTIC", "METAL", "ORGANIC"];
const BINS = new Set(BIN_KEYS);
const HISTORY_LIMIT = 20;

function nonnegativeCount(value) {
  return Number.isInteger(value) && value >= 0 ? value : null;
}

function predictionTime(value) {
  if (typeof value !== "string" || value.trim() === "") return null;
  const parsed = Date.parse(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function completedSorts(value) {
  if (!Array.isArray(value)) return null;
  const seen = new Set();
  const rows = [];
  for (const entry of value) {
    if (!entry || typeof entry !== "object" || Array.isArray(entry)) continue;
    const id = typeof entry.id === "string" || Number.isSafeInteger(entry.id)
      ? String(entry.id).trim().slice(0, 80) : "";
    const label = typeof entry.label === "string" ? entry.label.trim().slice(0, 80) : "";
    const bin = typeof entry.bin === "string" ? entry.bin.toUpperCase() : "";
    const completedAt = typeof entry.completed_at === "string" ? entry.completed_at.trim() : "";
    const completedMs = Date.parse(completedAt);
    if (!id || seen.has(id) || !label || label.toUpperCase() === "UNKNOWN" ||
        !BINS.has(bin) || !Number.isFinite(completedMs)) continue;
    seen.add(id);
    rows.push({ id, label, bin, completedAt: new Date(completedMs).toISOString(), completedMs });
  }
  return rows.sort((a, b) => b.completedMs - a.completedMs).slice(0, HISTORY_LIMIT)
    .map(({ completedMs, ...entry }) => entry);
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
    counts: Object.fromEntries(BIN_KEYS.map(binName => [binName,
      nonnegativeCount(rawCounts[binName] ?? rawCounts[binName.toLowerCase()])])),
    predictionAt: predictionTime(raw.prediction_at ?? raw.timestamp),
    recentSorts: completedSorts(raw.recent_sorts)
  };
}

export function derivePrediction(state, options = {}) {
  const backendOnline = options.backendOnline === true;
  const now = Number.isFinite(options.now) ? options.now : Date.now();
  const staleAfterMs = Number.isFinite(options.staleAfterMs) ? options.staleAfterMs : 8000;

  if (!backendOnline) {
    return { kind: "offline", label: "No current item", confidence: null, bin: null,
      note: "System unavailable. Predictions are hidden until it reconnects." };
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
  if (state.label.toUpperCase() === "UNKNOWN" || state.bin === null) {
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
      ? state.confidence === null
        ? "Latest API response. Capture time and model confidence score were not supplied."
        : "Latest API response. Capture time was not supplied."
      : state.confidence === null
        ? "Detection from the camera. No model confidence score was supplied."
        : "Detection from the camera."
  };
}

export function formatCount(value) {
  return value === null ? "—" : new Intl.NumberFormat().format(value);
}
