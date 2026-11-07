import { derivePrediction, formatCount, normalizeState } from "./state.mjs";

const byId = (id) => document.getElementById(id);
const ui = {
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
  recycleCount: byId("recycleCount"),
  trashCount: byId("trashCount")
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
let awaitingStopState = false;
let stopRequestedAt = null;
let frameRequestAt = null;
let stateRequestSequence = 0;
let stopMinimumSequence = 0;
let stopConfirmationTimer = null;

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
  }

  ui.armState.textContent = label;
  ui.armDescription.textContent = description;
  setBadge(ui.armBadge, badge, tone);
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
  ui.binDecision.textContent = prediction.bin === "RECYCLE"
    ? "Recycle bin" : prediction.bin === "TRASH" ? "Trash bin" : "No route selected";

  renderArm();
  ui.stopButton.disabled = stopPending || (!previewMode && !backendOnline);
  ui.stopButton.querySelector("span").textContent = stopPending ? "SENDING..." : "STOP ARM";
  ui.recycleCount.textContent = backendOnline && snapshot ? formatCount(snapshot.counts.RECYCLE) : "—";
  ui.trashCount.textContent = backendOnline && snapshot ? formatCount(snapshot.counts.TRASH) : "—";
}

function setupPreview() {
  const sample = {
    label: "Plastic bottle",
    confidence: 0.94,
    bin: "RECYCLE",
    arm_state: "READY",
    connected: true,
    counts: { RECYCLE: 4, TRASH: 2 },
    prediction_at: new Date().toISOString()
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
