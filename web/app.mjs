import { BIN_KEYS, derivePrediction, formatCount, normalizeState } from "./state.mjs";

const BIN_DISPLAY = {
  PAPER: { label: "Paper bin", short: "PA" },
  PLASTIC: { label: "Plastic bin", short: "PL" },
  METAL: { label: "Metal bin", short: "ME" },
  ORGANIC: { label: "Organic bin", short: "OR" }
};

const byId = (id) => document.getElementById(id);
const ui = {
  runtimeMode: byId("runtimeMode"),
  bridgeControls: byId("bridgeControls"),
  bridgeStatus: byId("bridgeStatus"),
  homeButton: byId("homeButton"),
  sortingButton: byId("sortingButton"),
  mockControls: byId("mockControls"),
  mockScenario: byId("mockScenario"),
  overviewSubtitle: byId("overviewSubtitle"),
  backendHealth: byId("backendHealth"),
  backendStatus: byId("backendStatus"),
  armHealth: byId("armHealth"),
  controllerStatus: byId("controllerStatus"),
  cameraHealth: byId("cameraHealth"),
  cameraStatus: byId("cameraStatus"),
  previewSettingsNote: byId("previewSettingsNote"),
  notice: byId("notice"),
  cameraFrame: byId("cameraFrame"),
  frameFallback: byId("frameFallback"),
  itemLabel: byId("itemLabel"),
  predictionNote: byId("predictionNote"),
  confidenceValue: byId("confidenceValue"),
  confidenceMeter: byId("confidenceMeter"),
  confidenceFill: byId("confidenceFill"),
  binDecision: byId("binDecision"),
  armBadge: byId("armBadge"),
  armState: byId("armState"),
  armDescription: byId("armDescription"),
  stopButton: byId("stopButton"),
  binCounts: Object.fromEntries(BIN_KEYS.map(bin => [bin, byId(bin.toLowerCase() + "Count")])),
  historyStatus: byId("historyStatus"),
  historyMessage: byId("historyMessage"),
  sortHistory: byId("sortHistory"),
  historyAnnouncement: byId("historyAnnouncement")
};

const previewMode = new URLSearchParams(window.location.search).get("demo") === "1";
const POLL_MS = 1200;
const RETRY_MS = 3000;
const REQUEST_TIMEOUT_MS = 3000;
const FRAME_STALE_MS = 4500;
const STOP_CONFIRM_TIMEOUT_MS = 5000;
let snapshot = null;
let backendOnline = false;
let cameraOnline = false;
let lastFrameAt = null;
let stopPending = false;
let controlPending = false;
let awaitingStopState = false;
let stopRequestedAt = null;
let frameRequestAt = null;
let stateRequestSequence = 0;
let stopMinimumSequence = 0;
let stopConfirmationTimer = null;
let renderedHistoryKey = null;
let latestHistoryId = null;

function setHealth(container, label, health, value) {
  container.dataset.health = health;
  label.textContent = value;
}

function setBadge(element, label, tone) {
  element.textContent = label;
  element.dataset.tone = tone;
}

function setNotice(message, tone = "info") {
  ui.notice.textContent = message;
  ui.notice.dataset.tone = tone;
  ui.notice.hidden = !message;
}

function showFrame(available) {
  ui.cameraFrame.hidden = !available;
  ui.frameFallback.hidden = available;
}

function renderArm() {
  let label = "Waiting";
  let description = "Checking the arm connection.";
  let tone = "neutral";
  let badge = "UNKNOWN";

  if (!backendOnline) {
    label = "Offline";
    description = "System connection unavailable. Use the physical power cutoff if motion must stop.";
    tone = "bad";
    badge = "API OFFLINE";
  } else if (!snapshot?.connected) {
    label = "Disconnected";
    description = "The arm controller is not connected to the laptop.";
    tone = "bad";
    badge = "NO CONTROLLER";
  } else {
    const states = {
      READY: ["Ready", "Waiting for a supported item and a sort command.", "good"],
      BUSY: ["Moving", "The arm is running a pick-and-drop sequence.", "warn"],
      DONE: ["Complete", "The controller reported the last move complete.", "good"],
      ERROR: ["Needs attention", "Check the arm before sending another command.", "bad"],
      UNKNOWN: ["Unknown", "The controller sent an unrecognized state.", "warn"]
    };
    [label, description, tone] = states[snapshot.armState];
    badge = snapshot.armState;
    if (snapshot.armState === "ERROR" && snapshot.armDetail) description = snapshot.armDetail;
  }

  ui.armState.textContent = label;
  ui.armDescription.textContent = description;
  setBadge(ui.armBadge, badge, tone);
}

function renderHistory() {
  const sorts = backendOnline ? snapshot?.recentSorts : null;
  const mode = previewMode ? "sample" : !backendOnline ? "offline" : sorts === null ? "unavailable" : "live";
  const key = JSON.stringify([mode, sorts]);
  if (key === renderedHistoryKey) return;
  renderedHistoryKey = key;

  ui.historyStatus.textContent = mode.toUpperCase();
  ui.historyStatus.dataset.tone = mode;
  const messages = {
    offline: "History is unavailable while the dashboard API is offline.",
    unavailable: "Waiting for completed-sort records from the backend.",
    live: "No completed sorts yet. Items appear after the arm finishes a drop.",
    sample: "Sample history will appear here."
  };
  ui.historyMessage.textContent = messages[mode];
  ui.historyMessage.hidden = Array.isArray(sorts) && sorts.length > 0;
  ui.sortHistory.hidden = !Array.isArray(sorts) || sorts.length === 0;

  const rows = [];
  for (const sort of sorts ?? []) {
    const row = document.createElement("li");
    row.className = "history-row";
    const icon = document.createElement("span");
    icon.className = "history-icon";
    icon.dataset.bin = sort.bin;
    icon.setAttribute("aria-hidden", "true");
    icon.textContent = BIN_DISPLAY[sort.bin].short;
    const item = document.createElement("strong");
    item.className = "history-item";
    item.textContent = sort.label;
    const bin = document.createElement("span");
    bin.className = "history-bin";
    bin.dataset.bin = sort.bin;
    bin.textContent = BIN_DISPLAY[sort.bin].label;
    const time = document.createElement("time");
    time.className = "history-time";
    time.dateTime = sort.completedAt;
    time.textContent = new Date(sort.completedAt).toLocaleString(undefined,
      { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
    row.append(icon, item, bin, time);
    rows.push(row);
  }
  ui.sortHistory.replaceChildren(...rows);
  const newest = sorts?.[0];
  if (!previewMode && newest && latestHistoryId !== null && newest.id !== latestHistoryId) {
    ui.historyAnnouncement.textContent = newest.label + " sorted into " +
      BIN_DISPLAY[newest.bin].label + ".";
  }
  if (newest) latestHistoryId = newest.id;
}

function render() {
  if (!previewMode && cameraOnline && (lastFrameAt === null || Date.now() - lastFrameAt > FRAME_STALE_MS)) {
    cameraOnline = false;
  }

  setHealth(ui.backendHealth, ui.backendStatus, backendOnline ? "good" : "bad",
    backendOnline ? (previewMode ? "Preview data" : "Online") : "Offline");
  ui.previewSettingsNote.hidden = !previewMode;
  setHealth(ui.armHealth, ui.controllerStatus,
    previewMode ? "waiting" : !backendOnline ? "waiting" : snapshot?.connected ? "good" : "bad",
    previewMode ? "Preview data" : !backendOnline ? "Unavailable" : snapshot?.connected ? "Connected" : "Disconnected");
  setHealth(ui.cameraHealth, ui.cameraStatus, cameraOnline ? "good" : "bad",
    cameraOnline ? "Connected" : "Disconnected");
  showFrame(backendOnline && cameraOnline);

  ui.overviewSubtitle.textContent = previewMode
    ? "Sample preview. No camera or arm hardware is connected."
    : "Follow the camera, bin decision, and robotic arm in one place.";

  const prediction = derivePrediction(snapshot, {
    backendOnline,
    cameraOnline,
    now: Date.now(),
    staleAfterMs: previewMode ? 86400000 : 8000
  });
  ui.itemLabel.textContent = prediction.label;
  ui.predictionNote.textContent = prediction.note;
  const percent = prediction.confidence === null ? null : Math.round(prediction.confidence * 100);
  ui.confidenceValue.textContent = percent === null ? "—" : percent + "%";
  ui.confidenceFill.style.width = percent === null ? "0%" : percent + "%";
  ui.confidenceMeter.setAttribute("aria-valuenow", percent === null ? "0" : String(percent));
  ui.confidenceMeter.setAttribute("aria-valuetext", percent === null ? "No confidence available" : percent + " percent");
  ui.binDecision.textContent = prediction.bin
    ? BIN_DISPLAY[prediction.bin].label : "No route selected";

  renderArm();
  const bridgeAvailable = !previewMode && backendOnline && snapshot?.bridgeAvailable;
  ui.bridgeControls.hidden = !bridgeAvailable;
  ui.bridgeStatus.hidden = !bridgeAvailable;
  ui.bridgeStatus.textContent = snapshot?.status ?? "";
  ui.runtimeMode.hidden = !previewMode && !snapshot?.simulation;
  ui.runtimeMode.textContent = previewMode ? "SAMPLE PREVIEW" : "SIMULATED ARM";
  ui.mockControls.hidden = !bridgeAvailable || !snapshot?.mockCamera;
  ui.homeButton.disabled = controlPending || !bridgeAvailable || !snapshot?.connected || snapshot?.armState === "BUSY";
  ui.sortingButton.disabled = controlPending || !bridgeAvailable || (!snapshot?.sortingEnabled && (!snapshot?.connected || snapshot?.armState !== "READY" || !cameraOnline));
  ui.sortingButton.textContent = snapshot?.sortingEnabled ? "Pause sorting" : "Start sorting";
  ui.stopButton.disabled = stopPending || (!previewMode && !backendOnline);
  ui.stopButton.querySelector("span").textContent = stopPending ? "SENDING..." : "STOP ARM";
  for (const bin of BIN_KEYS) {
    ui.binCounts[bin].textContent = backendOnline && snapshot
      ? formatCount(snapshot.counts[bin]) : "—";
  }
  renderHistory();
}

function setupPreview() {
  const sample = {
    label: "Plastic bottle",
    confidence: 0.94,
    bin: "PLASTIC",
    arm_state: "READY",
    connected: true,
    counts: { PAPER: 3, PLASTIC: 4, METAL: 2, ORGANIC: 1 },
    prediction_at: new Date().toISOString(),
    recent_sorts: [
      { id: "sample-4", label: "Plastic bottle", bin: "PLASTIC", completed_at: new Date(Date.now() - 120000).toISOString() },
      { id: "sample-3", label: "Paper sheet", bin: "PAPER", completed_at: new Date(Date.now() - 360000).toISOString() },
      { id: "sample-2", label: "Aluminum can", bin: "METAL", completed_at: new Date(Date.now() - 600000).toISOString() },
      { id: "sample-1", label: "Banana peel", bin: "ORGANIC", completed_at: new Date(Date.now() - 840000).toISOString() }
    ]
  };
  backendOnline = true;
  cameraOnline = true;
  snapshot = normalizeState(sample);
  ui.cameraFrame.addEventListener("error", () => {
    cameraOnline = false;
    render();
  });
  ui.cameraFrame.src = new URL("./assets/demo-bottle.svg?v=2", document.baseURI).href;
  render();
}

function refreshLiveFrame() {
  // Do not cancel a slow JPEG on every state poll. Retry only after its deadline.
  if (frameRequestAt !== null && Date.now() - frameRequestAt < FRAME_STALE_MS) return;
  frameRequestAt = Date.now();
  ui.cameraFrame.src = "/api/frame.jpg?t=" + frameRequestAt;
}

async function pollState() {
  const requestSequence = ++stateRequestSequence;
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  try {
    const response = await fetch("/api/state", { cache: "no-store", signal: controller.signal });
    if (!response.ok) throw new Error("State request failed: " + response.status);
    snapshot = normalizeState(await response.json());
    backendOnline = true;
    if (awaitingStopState && requestSequence >= stopMinimumSequence) {
      if (!snapshot.connected) {
        setNotice("Arm controller disconnected before STOP was confirmed. Use the physical power cutoff if needed.", "error");
        awaitingStopState = false;
      } else if (["READY", "DONE", "ERROR"].includes(snapshot.armState)) {
        setNotice(
          "Controller now reports " + snapshot.armState + ". Verify the arm is stationary before resuming.",
          snapshot.armState === "ERROR" ? "error" : "info"
        );
        awaitingStopState = false;
      } else if (stopRequestedAt !== null && Date.now() - stopRequestedAt > STOP_CONFIRM_TIMEOUT_MS) {
        setNotice("STOP was not confirmed after five seconds. Use the physical power cutoff if motion continues.", "error");
        awaitingStopState = false;
      }
    }
    refreshLiveFrame();
  } catch {
    if (awaitingStopState) {
      setNotice("Dashboard API was lost before STOP was confirmed. Use the physical power cutoff if needed.", "error");
      awaitingStopState = false;
    }
    backendOnline = false;
    cameraOnline = false;
    lastFrameAt = null;
    snapshot = null;
  } finally {
    clearTimeout(timeout);
    render();
    setTimeout(pollState, backendOnline ? POLL_MS : RETRY_MS);
  }
}

async function requestStop() {
  if (stopPending) return;
  if (previewMode) {
    setNotice("Sample preview only. No command was sent to hardware.");
    return;
  }
  if (!backendOnline) {
    setNotice("The dashboard API is offline. Use the physical servo power cutoff if the arm must stop.", "error");
    return;
  }
  stopPending = true;
  render();
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  try {
    const response = await fetch("/api/stop", { method: "POST", signal: controller.signal });
    if (!response.ok) throw new Error("Stop request failed: " + response.status);
    awaitingStopState = true;
    stopRequestedAt = Date.now();
    // Ignore responses from GETs already in flight when POST completed.
    stopMinimumSequence = stateRequestSequence + 1;
    clearTimeout(stopConfirmationTimer);
    stopConfirmationTimer = setTimeout(() => {
      if (!awaitingStopState) return;
      awaitingStopState = false;
      setNotice("STOP was not confirmed after five seconds. Use the physical power cutoff if motion continues.", "error");
    }, STOP_CONFIRM_TIMEOUT_MS);
    setNotice("STOP request sent. Waiting for the arm controller to report its state.", "success");
  } catch {
    setNotice("STOP request failed. Use the physical servo power cutoff if the arm must stop.", "error");
  } finally {
    clearTimeout(timeout);
    stopPending = false;
    render();
  }
}

async function bridgeCommand(path, body = {}) {
  if (previewMode || controlPending || !backendOnline) return;
  controlPending = true;
  render();
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  try {
    const response = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body), signal: controller.signal });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "The arm did not accept the command.");
    if (result.state) snapshot = normalizeState(result.state);
    setNotice(path === "/api/home" ? "Homing requested. Sorting remains paused." : "Control updated.");
  } catch (error) {
    setNotice(error.name === "AbortError" ? "Request timed out. Check controller state before retrying." : error.message, "error");
  } finally {
    clearTimeout(timeout);
    controlPending = false;
    render();
  }
}

ui.homeButton.addEventListener("click", () => bridgeCommand("/api/home"));
ui.sortingButton.addEventListener("click", () => bridgeCommand("/api/sorting", { enabled: !snapshot?.sortingEnabled }));
ui.mockScenario.addEventListener("change", () => bridgeCommand("/api/mock", { scenario: ui.mockScenario.value }));
ui.stopButton.addEventListener("click", requestStop);
if (previewMode) {
  setupPreview();
} else {
  ui.cameraFrame.addEventListener("load", () => {
    frameRequestAt = null;
    lastFrameAt = Date.now();
    cameraOnline = true;
    render();
  });
  ui.cameraFrame.addEventListener("error", () => {
    frameRequestAt = null;
    lastFrameAt = null;
    cameraOnline = false;
    render();
  });
  render();
  pollState();
}
