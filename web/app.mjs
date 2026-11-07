import { derivePrediction, formatCount, normalizeState } from "./state.mjs";

const byId = (id) => document.getElementById(id);
const ui = {
  modeBadge: byId("modeBadge"),
  sessionStatus: byId("sessionStatus"),
  lastUpdate: byId("lastUpdate"),
  backendHealth: byId("backendHealth"),
  backendStatus: byId("backendStatus"),
  espHealth: byId("espHealth"),
  espStatus: byId("espStatus"),
  cameraHealth: byId("cameraHealth"),
  cameraStatus: byId("cameraStatus"),
  notice: byId("notice"),
  cameraFrame: byId("cameraFrame"),
  frameFallback: byId("frameFallback"),
  frameBadge: byId("frameBadge"),
  frameSource: byId("frameSource"),
  frameHint: byId("frameHint"),
  detectionBadge: byId("detectionBadge"),
  itemLabel: byId("itemLabel"),
  predictionNote: byId("predictionNote"),
  confidenceValue: byId("confidenceValue"),
  confidenceMeter: byId("confidenceMeter"),
  confidenceFill: byId("confidenceFill"),
  binDecision: byId("binDecision"),
  binMarker: byId("binMarker"),
  armBadge: byId("armBadge"),
  armState: byId("armState"),
  armDescription: byId("armDescription"),
  stopButton: byId("stopButton"),
  recycleCount: byId("recycleCount"),
  trashCount: byId("trashCount"),
  demoPanel: byId("demoPanel"),
  scenarioButtons: byId("scenarioButtons")
};

const demoMode = new URLSearchParams(window.location.search).get("demo") === "1";
const POLL_MS = 1200;
const RETRY_MS = 3000;
const REQUEST_TIMEOUT_MS = 3000;
const FRAME_STALE_MS = 4500;
const STOP_CONFIRM_TIMEOUT_MS = 5000;
let snapshot = null;
let backendOnline = false;
let cameraOnline = false;
let lastFrameAt = null;
let lastResponseAt = null;
let stopPending = false;
let awaitingStopState = false;
let stopRequestedAt = null;
let activeScenario = "ready";
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
  if (!available) {
    ui.frameBadge.textContent = "FRAME UNAVAILABLE";
    ui.frameSource.textContent = "NO FRAME";
  } else {
    ui.frameBadge.textContent = demoMode ? "SAMPLE FRAME" : "CAMERA AVAILABLE";
    ui.frameSource.textContent = demoMode ? "DEMO IMAGE" : "LATEST FRAME";
  }
}

function renderArm() {
  let label = "Waiting";
  let description = "Checking the ESP32 connection.";
  let tone = "neutral";
  let badge = "UNKNOWN";

  if (!backendOnline) {
    label = "Offline";
    description = "Dashboard API unavailable. Use the physical power cutoff if motion must stop.";
    tone = "bad";
    badge = "API OFFLINE";
  } else if (!snapshot?.connected) {
    label = "Disconnected";
    description = "ESP32 is not connected to the laptop.";
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
  if (!demoMode && cameraOnline && (lastFrameAt === null || Date.now() - lastFrameAt > FRAME_STALE_MS)) {
    cameraOnline = false;
  }
  ui.modeBadge.dataset.mode = demoMode ? "demo" : "live";
  ui.modeBadge.innerHTML = '<span class="badge-dot"></span>' + (demoMode ? "DEMO MODE" : "LIVE SYSTEM");
  ui.demoPanel.hidden = !demoMode;
  ui.frameHint.textContent = demoMode
    ? "Sample illustration · no camera or hardware connected"
    : "Frame source: /api/frame.jpg";

  setHealth(ui.backendHealth, ui.backendStatus, backendOnline ? "good" : "bad",
    backendOnline ? (demoMode ? "Sample data" : "Online") : "Offline");
  setHealth(ui.espHealth, ui.espStatus, !backendOnline ? "waiting" : snapshot?.connected ? "good" : "bad",
    !backendOnline ? "Unavailable" : snapshot?.connected ? "Connected" : "Disconnected");
  setHealth(ui.cameraHealth, ui.cameraStatus, !backendOnline ? "waiting" : cameraOnline ? "good" : "bad",
    !backendOnline ? "Unavailable" : cameraOnline ? (demoMode ? "Sample frame" : "Available") : "No frame");
  showFrame(backendOnline && cameraOnline);

  if (demoMode) {
    ui.sessionStatus.textContent = "Previewing sample data";
    ui.lastUpdate.textContent = "No hardware commands are sent";
  } else if (!backendOnline) {
    ui.sessionStatus.textContent = "Dashboard API offline";
    ui.lastUpdate.textContent = "Waiting for /api/state";
  } else {
    ui.sessionStatus.textContent = snapshot?.connected ? "System connected" : "ESP32 disconnected";
    ui.lastUpdate.textContent = "Last API response " + lastResponseAt.toLocaleTimeString();
  }

  const prediction = derivePrediction(snapshot, {
    backendOnline,
    cameraOnline,
    now: Date.now(),
    staleAfterMs: demoMode && activeScenario !== "stale" ? 86400000 : 8000
  });
  const badgeMap = {
    offline: ["OFFLINE", "bad"],
    "camera-offline": ["NO CAMERA", "bad"],
    empty: ["NO DATA", "neutral"],
    stale: ["STALE", "warn"],
    review: ["REVIEW", "warn"],
    latest: ["LATEST RESPONSE", "warn"],
    detected: ["RECENT", "good"]
  };
  setBadge(ui.detectionBadge, ...badgeMap[prediction.kind]);
  ui.itemLabel.textContent = prediction.label;
  ui.predictionNote.textContent = prediction.note;
  const percent = prediction.confidence === null ? null : Math.round(prediction.confidence * 100);
  ui.confidenceValue.textContent = percent === null ? "—" : percent + "%";
  ui.confidenceFill.style.width = percent === null ? "0%" : percent + "%";
  ui.confidenceMeter.setAttribute("aria-valuenow", percent === null ? "0" : String(percent));
  ui.confidenceMeter.setAttribute("aria-valuetext", percent === null ? "No confidence available" : percent + " percent");
  ui.binDecision.textContent = prediction.bin === "RECYCLE"
    ? "Recycle bin" : prediction.bin === "TRASH" ? "Trash bin" : "No route selected";
  ui.binMarker.dataset.bin = prediction.bin ?? "none";
  ui.binMarker.textContent = prediction.bin === "RECYCLE" ? "R" : prediction.bin === "TRASH" ? "T" : "—";

  renderArm();
  ui.stopButton.disabled = stopPending || (!demoMode && !backendOnline);
  ui.stopButton.querySelector("span").textContent = stopPending ? "SENDING..." : "STOP ARM";
  ui.recycleCount.textContent = backendOnline && snapshot ? formatCount(snapshot.counts.RECYCLE) : "—";
  ui.trashCount.textContent = backendOnline && snapshot ? formatCount(snapshot.counts.TRASH) : "—";
}

function demoState(key) {
  const base = {
    label: "Plastic bottle",
    confidence: 0.94,
    bin: "RECYCLE",
    arm_state: "READY",
    connected: true,
    counts: { RECYCLE: 4, TRASH: 2 },
    prediction_at: new Date().toISOString()
  };
  switch (key) {
    case "trash":
      return { ...base, label: "Snack wrapper", confidence: 0.91, bin: "TRASH" };
    case "busy":
      return { ...base, arm_state: "BUSY" };
    case "done":
      return { ...base, arm_state: "DONE", counts: { RECYCLE: 5, TRASH: 2 } };
    case "unknown":
      return { ...base, label: "UNKNOWN", confidence: 0.22, bin: null };
    case "stale":
      return { ...base, prediction_at: new Date(Date.now() - 30000).toISOString() };
    case "error":
      return { ...base, arm_state: "ERROR" };
    case "esp-offline":
      return { ...base, connected: false, arm_state: "ERROR" };
    default:
      return base;
  }
}

const demoScenarios = [
  ["ready", "Ready"], ["trash", "Trash item"], ["busy", "Sorting"],
  ["done", "Completed"], ["unknown", "Unknown"], ["stale", "Stale"],
  ["error", "Arm error"], ["esp-offline", "ESP32 offline"],
  ["camera-offline", "Camera offline"], ["api-offline", "API offline"]
];

function selectDemoScenario(key) {
  activeScenario = key;
  backendOnline = key !== "api-offline";
  cameraOnline = backendOnline && key !== "camera-offline";
  snapshot = backendOnline ? normalizeState(demoState(key)) : null;
  lastResponseAt = backendOnline ? new Date() : null;
  if (cameraOnline) {
    const sampleFrame = new URL(
      key === "trash" ? "./assets/demo-wrapper.svg" : "./assets/demo-bottle.svg",
      document.baseURI
    ).href;
    if (ui.cameraFrame.src !== sampleFrame) ui.cameraFrame.src = sampleFrame;
  }
  for (const button of ui.scenarioButtons.querySelectorAll("button")) {
    button.setAttribute("aria-pressed", String(button.dataset.scenario === key));
  }
  setNotice("", "info");
  render();
}

function setupDemo() {
  for (const [key, label] of demoScenarios) {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = label;
    button.dataset.scenario = key;
    button.addEventListener("click", () => selectDemoScenario(key));
    ui.scenarioButtons.append(button);
  }
  ui.cameraFrame.addEventListener("error", () => {
    cameraOnline = false;
    render();
  });
  selectDemoScenario("ready");
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
    lastResponseAt = new Date();
    if (awaitingStopState && requestSequence >= stopMinimumSequence) {
      if (!snapshot.connected) {
        setNotice("ESP32 disconnected before STOP was confirmed. Use the physical power cutoff if needed.", "error");
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
  if (demoMode) {
    selectDemoScenario("error");
    setNotice("Demo STOP requested. No command was sent to hardware.", "success");
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
    setNotice("STOP request sent. Waiting for the ESP32 to report its state.", "success");
  } catch {
    setNotice("STOP request failed. Use the physical servo power cutoff if the arm must stop.", "error");
  } finally {
    clearTimeout(timeout);
    stopPending = false;
    render();
  }
}

ui.stopButton.addEventListener("click", requestStop);
if (demoMode) {
  setupDemo();
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
