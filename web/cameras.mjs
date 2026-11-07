import { chooseCameraDevice } from './camera-choice.mjs';
const previewMode = new URLSearchParams(location.search).get('demo') === '1';
const select = document.getElementById('usbCameraDevice');
const source = document.getElementById('cameraSourceSelect');
const refresh = document.getElementById('refreshCameraDevices');
const help = document.getElementById('cameraDeviceHelp');
const apply = document.getElementById('applyCameraButton');
let requestSequence = 0;
async function loadCameras({restoreActive = false} = {}) {
  const sequence = ++requestSequence;
  let kind = source.value;
  refresh.disabled = true;
  apply.disabled = true;
  help.textContent = 'Looking for connected cameras…';
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 10000);
  try {
    const response = await fetch('/api/cameras', {signal: controller.signal});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Camera list unavailable.');
    if (sequence !== requestSequence) return;
    if (restoreActive) {
      const stateResponse = await fetch('/api/state', {signal: controller.signal});
      const state = await stateResponse.json();
      if (sequence !== requestSequence) return;
      const active = (data.devices || []).find(d => d.id === state.camera_device_id);
      if (state.camera_source === 'usb' && active) {
        kind = /iphone|continuity/i.test(active.name) ? 'iphone' : 'usb';
        source.dataset.usbKind = kind;
        source.value = kind;
        lastKind = kind;
      }
    }
    const preferred = chooseCameraDevice(data.devices || [], kind);
    select.value = preferred?.id || '';
    help.textContent = preferred ? `Ready: ${preferred.name}. Click Use this camera.`
      : kind === 'iphone'
        ? 'iPhone not detected. Connect it by USB, accept Trust, enable Continuity Camera, lock the phone, then refresh.'
        : 'Laptop webcam not detected. Check camera access, then refresh.';
  } catch (error) {
    if (sequence === requestSequence) { select.value = ''; help.textContent = error.name === 'AbortError' ? 'Camera search timed out. Refresh to retry.' : error.message; }
  } finally {
    clearTimeout(timeout);
    if (sequence === requestSequence) {
      refresh.disabled = false;
      source.dispatchEvent(new Event('change'));
    }
  }
}
refresh.addEventListener('click', loadCameras);
// A custom event avoids recursively fetching when rendering updates source state.
let lastKind = source.value;
source.addEventListener('change', () => {
  if (source.value !== lastKind) {
    lastKind = source.value;
    if (['usb', 'iphone'].includes(lastKind)) loadCameras();
  }
});
if (!previewMode) loadCameras({restoreActive: true});
