"""Thread-safe decision gate. No camera, cloud, or hardware dependencies."""
from datetime import datetime, timedelta, timezone
import math
import threading
import time
import uuid

BINS = ('PAPER', 'PLASTIC', 'METAL', 'ORGANIC')


def validate_prediction(raw):
    if not isinstance(raw, dict):
        raise ValueError('Prediction must be an object')
    label = raw.get('label')
    present = raw.get('item_present')
    review = raw.get('needs_review')
    bin_name = raw.get('bin')
    confidence = raw.get('confidence')
    if not isinstance(label, str) or not label.strip():
        raise ValueError('Prediction needs a label')
    if type(present) is not bool or type(review) is not bool:
        raise ValueError('item_present and needs_review must be booleans')
    if bin_name is not None and bin_name not in BINS:
        raise ValueError('Unsupported bin')
    if confidence is not None and (type(confidence) not in (int, float)
            or not math.isfinite(confidence) or not 0 <= confidence <= 1):
        raise ValueError('Invalid confidence')
    if not present and (bin_name is not None or label.upper() != 'EMPTY'):
        raise ValueError('Empty pickup area must have label EMPTY and no bin')
    return dict(label=label.strip()[:80], item_present=present,
                needs_review=review, bin=bin_name, confidence=confidence)


class Bridge:
    def __init__(self, arm, *, simulation=False, mock_camera=False, mock_available=None,
                 camera_source=None, camera_url=None, camera_index=0, vision_ready=True,
                 allowed_bins=BINS, clock=time.monotonic, max_age=8, prediction_max_age=8):
        self.arm = arm
        self.allowed_bins = tuple(allowed_bins)
        self.pending_sort = None
        self.recent_sorts = []
        self.simulation = simulation
        self.mock_camera = mock_camera
        self.mock_available = mock_camera if mock_available is None else mock_available
        self.camera_source = camera_source or ('mock' if mock_camera else 'usb')
        self.camera_url = camera_url
        self.camera_index = camera_index
        self.vision_ready = vision_ready
        self.clock = clock
        self.max_age = max_age
        self.prediction_max_age = prediction_max_age
        self.lock = threading.RLock()
        self.enabled = False
        self.epoch = 0
        self.awaiting_clear = False
        self.empty_samples = 0
        self.last_evaluated_frame = None
        self.counts = dict.fromkeys(BINS, 0)
        self.prediction = None
        self.frame = None
        self.frame_at = None
        self.camera_error = None
        self.camera_switches_pending = 0
        self.camera_switching = False
        self.status = 'Home the arm, then start sorting.'
        self.arm.on_state(self.on_state)

    def on_state(self, state, detail):
        state = getattr(state, 'value', state)
        with self.lock:
            if state == 'DONE' and self.pending_sort and detail == 'SORT ' + self.pending_sort['bin']:
                record = self.pending_sort
                self.pending_sort = None
                self.counts[record['bin']] += 1
                self.recent_sorts.insert(0, dict(record, completed_at=datetime.now(timezone.utc).isoformat()))
                del self.recent_sorts[20:]
            elif state == 'ERROR':
                self.pending_sort = None
                self.enabled = False
                self.invalidate('Arm needs attention: ' + str(detail))

    def invalidate(self, message):
        # Caller holds lock. In-flight model results can no longer act.
        self.epoch += 1
        self.prediction = None
        self.status = message

    def camera_frame(self, frame):
        with self.lock:
            if self.camera_switching:
                return
            self.frame = frame
            self.frame_at = self.clock()
            if self.camera_error is not None and self.status == self.camera_error:
                self.status = 'Camera feed restored. Sorting remains paused.'
            self.camera_error = None

    def camera_lost(self, reason='Camera unavailable. Sorting paused.'):
        with self.lock:
            if self.camera_switching:
                return
            self.frame = None
            self.frame_at = None
            self.camera_error = reason
            self.enabled = False
            self.invalidate(reason)

    def prepare_camera_switch(self):
        """Stop new predictions before waiting on a slow in-flight capture."""
        with self.lock:
            self.camera_switches_pending += 1
            self.camera_switching = True
            self.enabled = False
            self.frame = None
            self.frame_at = None
            self.last_evaluated_frame = None
            self.invalidate('Camera source change requested. Sorting paused.')

    def cancel_camera_switch(self):
        with self.lock:
            self.camera_switches_pending = max(0, self.camera_switches_pending - 1)
            self.camera_switching = self.camera_switches_pending > 0
            self.invalidate('Camera source change failed. Sorting remains paused.')

    def set_camera_source(self, name, url=None, *, index=0, vision_ready=True):
        with self.lock:
            self.enabled = False
            self.camera_source = name
            self.camera_url = url
            self.camera_index = index
            self.mock_camera = name == 'mock'
            self.vision_ready = vision_ready
            self.frame = None
            self.frame_at = None
            self.camera_error = None
            self.camera_switches_pending = max(0, self.camera_switches_pending - 1)
            self.camera_switching = self.camera_switches_pending > 0
            self.last_evaluated_frame = None
            self.invalidate('Camera source changed. Sorting paused.')

    def fresh_frame(self):
        return self.frame_at is not None and self.clock() - self.frame_at <= self.max_age

    def set_sorting(self, enabled):
        with self.lock:
            if type(enabled) is not bool:
                raise ValueError('enabled must be boolean')
            if enabled and self.camera_switching:
                raise ValueError('Camera source is changing; wait for a current frame')
            if enabled and not self.vision_ready:
                raise ValueError('Gemini is not configured for this camera source; set GEMINI_API_KEY and GEMINI_MODEL')
            if enabled and set(self.allowed_bins) != set(BINS):
                raise ValueError('Arm controller must support PAPER, PLASTIC, METAL, ORGANIC before sorting')
            if enabled and (self.arm.state != 'READY' or not self.arm.connected or not self.fresh_frame()):
                raise ValueError('Start requires a homed, connected arm and a current camera frame')
            self.enabled = enabled
            self.invalidate('Sorting enabled.' if enabled else 'Sorting paused. Current motion can finish; use STOP to interrupt.')

    def home(self):
        with self.lock:
            self.enabled = False
            self.invalidate('Homing requested. Sorting remains paused.')
            return self.arm.home()

    def stop(self):
        with self.lock:
            self.enabled = False
            self.invalidate('STOP requested. Home the arm before resuming.')
        # Never wait for a worker callback while holding the bridge lock.
        return self.arm.stop()

    def begin_prediction(self):
        with self.lock:
            if (not self.enabled or self.camera_switching or not self.vision_ready or self.arm.state != 'READY'
                    or not self.arm.connected or not self.fresh_frame()):
                return None
            age = self.clock() - self.frame_at
            timestamp = (datetime.now(timezone.utc) - timedelta(seconds=age)).isoformat()
            return self.epoch, self.frame_at, timestamp, self.frame

    def prediction_failed(self, ticket, reason=None):
        with self.lock:
            if ticket[0] == self.epoch:
                self.enabled = False
                self.invalidate(reason or 'Prediction unavailable. Check the camera/API and restart sorting.')

    def accept_prediction(self, ticket, raw):
        result = validate_prediction(raw)
        epoch, captured_at, timestamp, _ = ticket
        with self.lock:
            if epoch == self.epoch and self.clock() - captured_at > self.prediction_max_age:
                self.enabled = False
                self.invalidate('Classification arrived too late. Keep the item still and restart sorting.')
                return False
            if (epoch != self.epoch or not self.enabled or not self.arm.connected
                    or self.arm.state != 'READY' or not self.fresh_frame()
                                        or captured_at == self.last_evaluated_frame):
                return False
            self.last_evaluated_frame = captured_at
            self.prediction = dict(result, prediction_at=timestamp, captured_at=captured_at)
            if not result['item_present']:
                self.empty_samples += 1
                if self.empty_samples >= 2:
                    self.awaiting_clear = False
                self.status = 'Pickup area empty.'
                return False
            self.empty_samples = 0
            if (self.awaiting_clear or result['needs_review'] or result['bin'] is None
                    or result['label'].upper() == 'UNKNOWN'
                    or (result['confidence'] is not None and result['confidence'] < .8)):
                self.status = 'Waiting for pickup area to clear.' if self.awaiting_clear else 'Item needs review; no motion requested.'
                return False
            if result['bin'] not in self.allowed_bins:
                return False
            self.pending_sort = dict(id=str(uuid.uuid4()), label=result['label'], bin=result['bin'])
            accepted = self.arm.sort(result['bin'])
            if accepted:
                self.awaiting_clear = True
                self.epoch += 1
                self.status = 'Sorting into ' + result['bin'] + '.'
            else:
                self.pending_sort = None
                self.status = self.arm.last_rejection or 'Arm rejected sort.'
            return accepted

    def snapshot(self):
        with self.lock:
            pred = self.prediction
            if not self.fresh_frame() or (pred and self.clock() - pred['captured_at'] > self.max_age):
                pred = None
            route = pred['bin'] if pred and pred['item_present'] and not pred['needs_review'] and pred['label'].upper() != 'UNKNOWN' else None
            if pred and pred['confidence'] is not None and pred['confidence'] < .8:
                route = None
            return dict(label=pred['label'] if pred else 'UNKNOWN',
                        confidence=pred['confidence'] if pred else None, bin=route,
                        prediction_at=pred['prediction_at'] if pred else None,
                        arm_state=getattr(self.arm.state, 'value', self.arm.state),
                        connected=self.arm.connected, counts=dict(self.counts),
                        recent_sorts=[dict(row) for row in self.recent_sorts],
                        simulation=self.simulation, mock_camera=self.mock_camera,
                        mock_available=self.mock_available, camera_source=self.camera_source,
                        camera_url=self.camera_url, camera_index=self.camera_index,
                        camera_error=self.camera_error,
                        vision_ready=self.vision_ready, sorting_enabled=self.enabled,
                        status=self.status, arm_detail=self.arm.detail,
                        awaiting_clear=self.awaiting_clear)
