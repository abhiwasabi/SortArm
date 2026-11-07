import { BIN_KEYS, derivePrediction, formatCount, normalizeState } from "./state.mjs";

const BIN_DISPLAY = {
  PAPER: { label: "Paper bin", short: "PA" },
  PLASTIC: { label: "Plastic bin", short: "PL" },
  METAL: { label: "Metal bin", short: "ME" },
  ORGANIC: { label: "Organic bin", short: "OR" }
};

const byId = (id) => document.getElementById(id);
const ui = {
  toggleCameraButton: byId("toggleCameraButton"),
  stopCameraSettings: byId("stopCameraSettings"),
  runtimeMode: byId("runtimeMode"),
  bridgeControls: byId("bridgeControls"),
  bridgeStatus: byId("bridgeStatus"),
  homeButton: byId("homeButton"),
  sortingButton: byId("sortingButton"),
  manualSortCard: byId("manualSortCard"),
  manualSortButtons: byId("manualSortButtons"),
  mockControls: byId("mockControls"),
  mockScenario: byId("mockScenario"),
  cameraSourceCard: byId("cameraSourceCard"),
  cameraSourceSelect: byId("cameraSourceSelect"),
  usbCameraFields: byId("usbCameraFields"),
  usbCameraDevice: byId("usbCameraDevice"),
  esp32CameraFields: byId("esp32CameraFields"),
  esp32CameraUrl: byId("esp32CameraUrl"),
  applyCameraButton: byId("applyCameraButton"),
  cameraSourceStatus: byId("cameraSourceStatus"),
  cameraSourceError: byId("cameraSourceError"),
  overviewSubtitle: byId("overviewSubtitle"),
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
const FRAME_POLL_MS = 100;
const RETRY_MS = 3000;
const REQUEST_TIMEOUT_MS = 3000;
const STOP_REQUEST_TIMEOUT_MS = 8000;
const FRAME_REQUEST_TIMEOUT_MS = 6500;
const FRAME_STALE_MS = 10000;
const STOP_CONFIRM_TIMEOUT_MS = 5000;
let snapshot = null;
let backendOnline = false;
let cameraOnline = false;
let lastFrameAt = null;
let stopPending = false;
let controlPending = false;
let cameraChangePending = false;
let cameraSelectionDirty = false;
let awaitingStopState = false;
let stopRequestedAt = null;
let frameRequestAt = null;
let stateRequestSequence = 0;
let stopMinimumSequence = 0;
let stopConfirmationTimer = null;
let renderedHistoryKey = null;
let latestHistoryId = null;
let manualBinsKey = null;
let manualButtons = [];

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

function renderCameraSource(usableCamera) {
  ui.cameraSourceCard.hidden = previewMode;
  if (previewMode) return;
  const available = backendOnline && snapshot?.bridgeAvailable;
  const cameraOff = snapshot?.cameraSource === "off";
  ui.toggleCameraButton.textContent = cameraOff ? "Start camera" : "Stop camera";
  ui.toggleCameraButton.hidden = previewMode;
  ui.toggleCameraButton.disabled = !available || cameraChangePending;
  ui.stopCameraSettings.hidden = cameraOff;
  ui.stopCameraSettings.disabled = !available || cameraChangePending;
  if (!cameraSelectionDirty && available && snapshot.cameraSource !== "off") {
    ui.cameraSourceSelect.value = snapshot.cameraSource === "mock" ? "usb" :
      snapshot.cameraSource === "usb" && ui.cameraSourceSelect.dataset.usbKind === "iphone" ? "iphone" : snapshot.cameraSource;
    if (snapshot.cameraSource === "esp32") ui.esp32CameraUrl.value = snapshot.cameraUrl ?? "";
  }
  const selection = ui.cameraSourceSelect.value;
  ui.esp32CameraFields.hidden = selection !== "esp32";
  ui.usbCameraFields.hidden = !["usb", "iphone"].includes(selection);
  ui.cameraSourceSelect.disabled = !available || controlPending || cameraChangePending;
  ui.esp32CameraUrl.disabled = !available || controlPending || cameraChangePending;
  ui.applyCameraButton.disabled = !available || controlPending || cameraChangePending ||
    (selection === "esp32" && !ui.esp32CameraUrl.value.trim()) ||
    (["usb", "iphone"].includes(selection) && !ui.usbCameraDevice.value);
  ui.applyCameraButton.textContent = cameraChangePending ? "Connecting..." : "Use this camera";
  const sourceError = available && snapshot.cameraSource !== "mock" ? snapshot.cameraError : null;
  ui.cameraSourceError.textContent = sourceError ?? "";
  ui.cameraSourceError.hidden = !sourceError;
  ui.cameraSourceStatus.textContent = !backendOnline ? "Dashboard API offline" :
    !available ? "Camera selection unavailable" :
    cameraOff ? "Camera off" :
    snapshot.cameraSource === "mock" ? "Simulated camera" :
    sourceError ? snapshot.cameraSource === "esp32" ? "ESP32 unavailable" : "Camera unavailable" :
    snapshot.cameraSource === "esp32" ?
      (usableCamera ? "ESP32 connected" : "Waiting for ESP32 frames") +
        (snapshot.visionReady ? " · Gemini configured" : " · Preview only") :
      usableCamera ? "Camera connected" : "Waiting for camera frames";
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
    description = snapshot?.armDetail || "The arm controller is not connected to the laptop.";
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
    if (snapshot.armState === "ERROR" && armNotResponding(snapshot)) {
      // HOME is refused until the stuck move ends, so do not suggest it.
      description = snapshot.armDetail + ". Use the physical power cutoff if it is still moving.";
    } else if (snapshot.armState === "ERROR") {
      description = (snapshot.armDetail || description) + " Press Home arm to recover.";
    } else if (snapshot.armState === "BUSY" && snapshot.armDetail) {
      description = "Moving: " + snapshot.armDetail;
    }
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

function manualSortAvailable() {
  return !previewMode && backendOnline && snapshot?.bridgeAvailable === true &&
    snapshot.connected && snapshot.armState === "READY" && !snapshot.sortingEnabled &&
    !controlPending && !stopPending && !awaitingStopState;
}

function renderManualSort(bridgeAvailable) {
  ui.manualSortCard.hidden = !bridgeAvailable;
  const bins = snapshot?.bins ?? BIN_KEYS;
  const key = JSON.stringify(bins);
  if (key !== manualBinsKey) {
    manualBinsKey = key;
    manualButtons = bins.map(bin => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "control-button";
      button.dataset.bin = bin;
      button.textContent = BIN_DISPLAY[bin]?.label ?? bin;
      button.addEventListener("click", () => bridgeCommand("/api/sort", { bin }));
      return button;
    });
    ui.manualSortButtons.replaceChildren(...manualButtons);
  }
  const enabled = manualSortAvailable();
  for (const button of manualButtons) button.disabled = !enabled;
}

function render() {
  if (!previewMode && cameraOnline && (lastFrameAt === null || Date.now() - lastFrameAt > FRAME_STALE_MS)) {
    cameraOnline = false;
  }
  const usableCamera = cameraOnline && !snapshot?.cameraError;

  ui.previewSettingsNote.hidden = !previewMode;
  setHealth(ui.cameraHealth, ui.cameraStatus, usableCamera ? "good" : "bad",
    usableCamera ? "Connected" : "Disconnected");
  showFrame(backendOnline && usableCamera);

  ui.overviewSubtitle.textContent = previewMode
    ? "Sample preview. No camera or arm hardware is connected."
    : "Follow the camera, bin decision, and robotic arm in one place.";

  const prediction = derivePrediction(snapshot, {
    backendOnline,
    cameraOnline: usableCamera,
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
  const bridgeAvailable = !previewMode && backendOnline && snapshot?.bridgeAvailable === true;
  ui.bridgeControls.hidden = !bridgeAvailable;
  ui.bridgeStatus.hidden = !bridgeAvailable;
  ui.bridgeStatus.textContent = snapshot?.status ?? "";
  ui.runtimeMode.hidden = !previewMode && !snapshot?.simulation;
  ui.runtimeMode.textContent = previewMode ? "SAMPLE PREVIEW" : "SIMULATED ARM";
  ui.mockControls.hidden = !bridgeAvailable || !snapshot?.mockCamera;
  renderCameraSource(usableCamera);
  const motionPending = controlPending || stopPending || awaitingStopState;
  ui.homeButton.disabled = motionPending || !bridgeAvailable || !snapshot?.connected || snapshot?.armState === "BUSY";
  ui.sortingButton.disabled = motionPending || !bridgeAvailable || (!snapshot?.sortingEnabled && (!snapshot?.connected || snapshot?.armState !== "READY" || !usableCamera || !snapshot?.visionReady));
  ui.sortingButton.textContent = snapshot?.sortingEnabled ? "Pause sorting" : "Start sorting";
  renderManualSort(bridgeAvailable);
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
  if (!backendOnline || snapshot?.cameraError) return;
  // Do not cancel a slow JPEG on every state poll. Retry only after its deadline.
  if (frameRequestAt !== null && Date.now() - frameRequestAt < FRAME_REQUEST_TIMEOUT_MS) return;
  frameRequestAt = Date.now();
  ui.cameraFrame.src = "/api/frame.jpg?t=" + frameRequestAt;
}

function refreshLiveFrameLoop() {
  refreshLiveFrame();
  setTimeout(refreshLiveFrameLoop, FRAME_POLL_MS);
}

// The arm controller reports "stopped; arm not responding" when STOP gave up waiting for a move
// stuck on the servo bus. That is not a confirmed stop.
function armNotResponding(state) {
  return /not responding/i.test(state?.armDetail || "");
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
    if (snapshot.cameraError) {
      cameraOnline = false;
      lastFrameAt = null;
      frameRequestAt = null;
    }
    if (awaitingStopState && requestSequence >= stopMinimumSequence) {
      if (!snapshot.connected) {
        setNotice("Arm controller disconnected before STOP was confirmed. Use the physical power cutoff if needed.", "error");
        awaitingStopState = false;
      } else if (snapshot.armState === "ERROR" && armNotResponding(snapshot)) {
        // The controller gave up waiting for a move stuck on the servo bus: motion may not have ended.
        setNotice("STOP was sent, but the arm is not responding and may still be moving. Use the physical power cutoff now.", "error");
        awaitingStopState = false;
      } else if (snapshot.armState === "ERROR") {
        setNotice("Arm stopped. Check it is clear, then press Home arm to recover.", "success");
        awaitingStopState = false;
      } else if (stopRequestedAt !== null && Date.now() - stopRequestedAt > STOP_CONFIRM_TIMEOUT_MS) {
        setNotice("STOP was not confirmed after five seconds. Use the physical power cutoff if motion continues.", "error");
        awaitingStopState = false;
      }
    }
    if (!snapshot.cameraError) refreshLiveFrame();
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
  const timeout = setTimeout(() => controller.abort(), STOP_REQUEST_TIMEOUT_MS);
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
      render();
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
  if (previewMode || controlPending || stopPending || awaitingStopState || !backendOnline) return;
  if (path === "/api/sort" && (!manualSortAvailable() || !snapshot.bins.includes(body.bin))) return;
  controlPending = true;
  render();
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  try {
    const response = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body), signal: controller.signal });
    const result = await response.json();
    if (result.state) snapshot = normalizeState(result.state);
    if (!response.ok || result.accepted === false) {
      throw new Error(typeof result.error === "string" && result.error.trim()
        ? result.error : "The arm did not accept the command.");
    }
    setNotice(path === "/api/home" ? "Homing requested. Sorting remains paused." :
      path === "/api/sort" ? "Manual sort requested for " + (BIN_DISPLAY[body.bin]?.label ?? body.bin) + "." : "Control updated.");
  } catch (error) {
    setNotice(error.name === "AbortError" ? "Request timed out. Check controller state before retrying." : error.message, "error");
  } finally {
    clearTimeout(timeout);
    controlPending = false;
    render();
  }
}

async function applyCameraSource() {
  if (previewMode || controlPending || cameraChangePending || !backendOnline || !snapshot?.bridgeAvailable) return;
  const source = ui.cameraSourceSelect.value;
  if (["usb", "iphone"].includes(source) && !ui.usbCameraDevice.value) {
    setNotice("Selected camera is unavailable or still being detected. Refresh the camera list and try again.", "error");
    return;
  }
  const body = { source: source === "iphone" ? "usb" : source };
  if (source === "esp32") body.url = ui.esp32CameraUrl.value.trim();
  if (["usb", "iphone"].includes(source)) {
    body.index = 0;
    if (ui.usbCameraDevice.value) body.device_id = ui.usbCameraDevice.value;
  }
  cameraChangePending = true;
  render();
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  try {
    const response = await fetch("/api/camera", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body), signal: controller.signal
    });
    const result = await response.json();
    if (!response.ok || result.accepted !== true) throw new Error(result.error || "Camera source was not accepted.");
    snapshot = normalizeState(result.state);
    ui.cameraSourceSelect.dataset.usbKind = source;
    cameraSelectionDirty = false;
    cameraOnline = false;
    lastFrameAt = null;
    frameRequestAt = null;
    ui.cameraFrame.removeAttribute("src");
    setNotice(source === "esp32" ?
      "ESP32 camera selected. Waiting for JPEG frames; sorting is paused." :
      "Camera changed. Sorting is paused.", "success");
  } catch (error) {
    setNotice(error.name === "AbortError" ?
      "Camera request timed out. Check the dashboard connection." : error.message, "error");
  } finally {
    clearTimeout(timeout);
    cameraChangePending = false;
    render();
  }
}

async function stopCamera() {
  if (previewMode || !backendOnline || cameraChangePending) return;
  cameraChangePending = true;
  render();
  try {
    const response = await fetch('/api/camera', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({source: 'off'})
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'Could not stop camera.');
    snapshot = normalizeState(result.state);
    frameRequestAt = lastFrameAt = null;
    cameraOnline = false;
    ui.cameraFrame.removeAttribute('src');
    showFrame(false);
    setNotice('Camera stopped. Sorting paused.');
  } catch (error) { setNotice(error.message, 'error'); }
  finally { cameraChangePending = false; render(); }
}
ui.toggleCameraButton.addEventListener('click', () => snapshot?.cameraSource === 'off' ? applyCameraSource() : stopCamera());
ui.stopCameraSettings.addEventListener('click', stopCamera);
ui.homeButton.addEventListener("click", () => bridgeCommand("/api/home"));
ui.sortingButton.addEventListener("click", () => bridgeCommand("/api/sorting", { enabled: !snapshot?.sortingEnabled }));
ui.mockScenario.addEventListener("change", () => bridgeCommand("/api/mock", { scenario: ui.mockScenario.value }));
ui.cameraSourceSelect.addEventListener("change", () => { cameraSelectionDirty = true; render(); });
ui.esp32CameraUrl.addEventListener("input", () => { cameraSelectionDirty = true; render(); });
ui.applyCameraButton.addEventListener("click", applyCameraSource);
ui.stopButton.addEventListener("click", requestStop);
if (previewMode) {
  setupPreview();
} else {
  ui.cameraFrame.addEventListener("load", () => {
    if (frameRequestAt === null || !backendOnline || snapshot?.cameraError) return;
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
  setTimeout(refreshLiveFrameLoop, FRAME_POLL_MS);
}
