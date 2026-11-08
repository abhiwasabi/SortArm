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
                 allowed_bins=BINS, clock=time.monotonic, max_age=8, prediction_max_age=8,
                 sort_log=None):
        self.arm = arm
        # Optional SortLog (bridge/sort_log.py). emit() only enqueues, so it is safe under the lock.
        self.sort_log = sort_log
        self.stop_sort_id = None
        self.allowed_bins = tuple(allowed_bins)
        self.pending_sort = None
        self.recent_sorts = []
        self.simulation = simulation
        self.continuous_sorting = simulation
        self.auto_sort_armed = simulation
        self.home_confirmed = False
        self.mock_camera = mock_camera
        self.mock_available = mock_camera if mock_available is None else mock_available
        self.camera_source = camera_source or ('mock' if mock_camera else 'usb')
        self.camera_url = camera_url
        self.camera_index = camera_index
        self.camera_device_id = None
        self.vision_ready = vision_ready
        self.clock = clock
        self.max_age = max_age
        self.prediction_max_age = prediction_max_age
        self.lock = threading.RLock()
        self._local = threading.local()  # per-request-thread rejection reason
        self.enabled = False
        self.epoch = 0
        self.awaiting_clear = False
        self.empty_samples = 0
        self.last_sorted_signature = None
        self.manual_item_in_view = False
        self.last_evaluated_frame = None
        self.counts = dict.fromkeys(BINS, 0)
        self.prediction = None
        self.frame = None
        self.frame_at = None
        self.camera_error = None
        self.camera_switches_pending = 0
        self.camera_switching = False
        self.status = ('Home the arm; automatic sorting starts when ready.' if simulation
                       else 'Home the arm, then start sorting.')
        self.arm.on_state(self.on_state)

    def on_state(self, state, detail):
        state = getattr(state, 'value', state)
        with self.lock:
            if state == 'DONE' and self.pending_sort and detail == 'SORT ' + self.pending_sort['bin']:
                record = self.pending_sort
                self.pending_sort = None
                self.counts[record['bin']] += 1
                completed_at = datetime.now(timezone.utc)
                self.recent_sorts.insert(0, dict(id=record['id'], label=record['label'], bin=record['bin'],
                                                 completed_at=completed_at.isoformat()))
                del self.recent_sorts[20:]
                self._log_sort(record, 'DONE', completed_at)
                if record.get('label') == 'Manual':
                    self.status = 'Manual sort to ' + record['bin'] + ' done.'
            elif state == 'ERROR':
                record = self.pending_sort
                if record is not None:
                    # Every STOP reports ERROR; a sort only counts as STOPPED if STOP was requested for it.
                    stopped = (record['id'] == self.stop_sort_id
                               or str(detail or '').lower().startswith('stopped'))
                    self._log_sort(record, 'STOPPED' if stopped else 'FAULT',
                                   datetime.now(timezone.utc), detail)
                self.stop_sort_id = None
                self.pending_sort = None
                self.enabled = False
                self.auto_sort_armed = False
                self.home_confirmed = False
                self.invalidate('Arm needs attention: ' + str(detail))

    def _begin_sort(self, label, bin_name, confidence=None):
        # Caller holds lock. One place to create pending_sort, so every path records its start time.
        self.pending_sort = dict(id=str(uuid.uuid4()), label=label, bin=bin_name,
                                 confidence=confidence, started_at=self.clock())
        return self.pending_sort

    def _log_sort(self, record, outcome, at, detail=None):
        # Caller holds lock. Only enqueues; a logging failure must never affect sorting.
        if self.sort_log is None:
            return
        try:
            started = record.get('started_at')
            self.sort_log.emit(dict(
                time=at, sort_id=record['id'], label=record['label'], bin=record['bin'],
                confidence=record.get('confidence'), outcome=outcome,
                cycle_s=None if started is None else max(0.0, self.clock() - started),
                simulated=self.simulation, detail=None if detail is None else str(detail)[:200]))
        except Exception:
            pass

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
                self.enabled = self.auto_sort_armed and self.home_confirmed and self.vision_ready
                self.status = ('Camera feed restored. Automatic sorting ready.' if self.enabled
                               else 'Camera feed restored. Sorting remains paused.')
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
            self.enabled = self.auto_sort_armed and self.home_confirmed and vision_ready and name != 'off'
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
            self.invalidate('Camera source changed. Automatic sorting ready.' if self.enabled
                            else 'Camera source changed. Sorting paused.')

    def fresh_frame(self):
        return self.frame_at is not None and self.clock() - self.frame_at <= self.max_age

    def set_sorting(self, enabled):
        with self.lock:
            if type(enabled) is not bool:
                raise ValueError('enabled must be boolean')
            if enabled and self.camera_switching:
                raise ValueError('Camera source is changing; wait for a current frame')
            if enabled and not self.home_confirmed:
                raise ValueError('Home the arm before sorting')
            if enabled and not self.vision_ready:
                raise ValueError('Gemini is not configured for this camera source; set GEMINI_API_KEY and GEMINI_MODEL')
            if enabled and set(self.allowed_bins) != set(BINS):
                raise ValueError('Arm controller must support PAPER, PLASTIC, METAL, ORGANIC before sorting')
            if enabled and (self.arm.state != 'READY' or not self.arm.connected or not self.fresh_frame()):
                raise ValueError('Start requires a homed, connected arm and a current camera frame')
            self.enabled = enabled
            if self.continuous_sorting:
                self.auto_sort_armed = enabled
            self.invalidate('Sorting enabled.' if enabled else 'Sorting paused. Current motion can finish; use STOP to interrupt.')

    def rejection_reason(self):
        """Why this thread's last home()/manual_sort() was refused: the bridge's own reason, else the arm's."""
        return getattr(self._local, 'rejection', None) or self.arm.last_rejection

    def home(self):
        with self.lock:
            self._local.rejection = None
            self.enabled = False
            self.invalidate('Homing requested. Sorting remains paused.')
            accepted = self.arm.home()
            if not accepted:
                self.home_confirmed = False
                self.auto_sort_armed = False
                self.status = self.arm.last_rejection or 'Arm rejected HOME.'
                return False
            self.home_confirmed = True
            if self.continuous_sorting:
                self.auto_sort_armed = True
            self.enabled = self.auto_sort_armed and self.vision_ready and self.camera_source != 'off'
            self.status = ('Homing requested. Automatic sorting will start when ready.' if self.enabled
                           else 'Homing requested. Sorting remains paused.')
            return True

    def manual_sort(self, bin_name):
        with self.lock:
            self._local.rejection = None
            if not isinstance(bin_name, str) or bin_name not in self.allowed_bins:
                raise ValueError('bin must be one of: ' + ', '.join(self.allowed_bins))
            if self.enabled:
                raise ValueError('Pause automatic sorting before a manual sort.')
            if self.pending_sort is not None:
                # The arm reports READY just before its DONE reaches on_state. A new sort accepted in
                # that gap would take over the finished sort's record: the late DONE would count the
                # new sort early (same bin) or the finished sort would never be counted (other bin).
                self._local.rejection = 'The previous sort is still finishing; try again in a moment.'
                self.status = self._local.rejection
                return False
            # Keep whatever was pending: if the arm rejects this request because an earlier sort
            # is still running, that sort must still be counted when it reports DONE.
            previous_sort, previous_clear = self.pending_sort, self.awaiting_clear
            self._begin_sort('Manual', bin_name)
            self.awaiting_clear = True
            self.invalidate('Manual sort to ' + bin_name + ' requested.')
            accepted = self.arm.sort(bin_name)
            if not accepted:
                self.pending_sort, self.awaiting_clear = previous_sort, previous_clear
                self.status = self.arm.last_rejection or 'Arm rejected the manual sort.'
            elif self.continuous_sorting:
                # Sorting is paused, so the item just sorted by hand was never classified. Treat the
                # next item seen after Start as that item, so it is not sorted (and counted) twice.
                self.manual_item_in_view = True
            return accepted

    def stop(self):
        with self.lock:
            self.enabled = False
            self.auto_sort_armed = False
            self.home_confirmed = False
            if self.pending_sort is not None:
                self.stop_sort_id = self.pending_sort['id']
            self.invalidate('STOP requested. Home the arm before resuming.')
        # Never wait for a worker callback while holding the bridge lock.
        return self.arm.stop()

    def begin_prediction(self):
        with self.lock:
            arm_can_classify = self.arm.state == 'READY' or (self.continuous_sorting
                                and self.arm.state == 'BUSY' and self.pending_sort is not None)
            if (not self.enabled or self.camera_switching or not self.vision_ready
                    or not arm_can_classify or not self.arm.connected or not self.fresh_frame()):
                return None
            age = self.clock() - self.frame_at
            timestamp = (datetime.now(timezone.utc) - timedelta(seconds=age)).isoformat()
            return self.epoch, self.frame_at, timestamp, self.frame

    def prediction_failed(self, ticket, reason=None, *, retryable=False):
        with self.lock:
            if ticket[0] == self.epoch:
                if not (retryable and self.continuous_sorting and self.auto_sort_armed):
                    self.enabled = False
                    self.auto_sort_armed = False
                message = reason or 'Prediction unavailable. Check the camera/API and restart sorting.'
                self.invalidate(message + ' Retrying automatically.' if self.enabled else message)

    def accept_prediction(self, ticket, raw):
        result = validate_prediction(raw)
        epoch, captured_at, timestamp, _ = ticket
        with self.lock:
            if epoch == self.epoch and self.clock() - captured_at > self.prediction_max_age:
                self.enabled = False
                self.invalidate('Classification arrived too late. Keep the item still and restart sorting.')
                return False
            arm_can_classify = self.arm.state == 'READY' or (self.continuous_sorting
                                and self.arm.state == 'BUSY' and self.pending_sort is not None)
            if (epoch != self.epoch or not self.enabled or not self.arm.connected
                    or not arm_can_classify or not self.fresh_frame()
                                        or captured_at == self.last_evaluated_frame):
                return False
            self.last_evaluated_frame = captured_at
            self.prediction = dict(result, prediction_at=timestamp, captured_at=captured_at)
            if not result['item_present']:
                self.empty_samples += 1
                if self.empty_samples >= 2:
                    self.awaiting_clear = False
                    self.last_sorted_signature = None
                    self.manual_item_in_view = False
                self.status = 'Pickup area empty.'
                return False
            self.empty_samples = 0
            if (result['needs_review'] or result['bin'] is None
                    or result['label'].upper() == 'UNKNOWN'
                    or (result['confidence'] is not None and result['confidence'] < .8)):
                self.status = 'Item needs review; no motion requested.'
                return False
            signature = (result['label'].casefold(), result['bin'])
            if self.continuous_sorting and self.manual_item_in_view:
                self.manual_item_in_view = False
                self.last_sorted_signature = signature
            if self.continuous_sorting and signature == self.last_sorted_signature:
                self.awaiting_clear = True
                self.status = 'Already sorted this item. Show a different item.'
                return False
            if not self.continuous_sorting and self.awaiting_clear:
                self.status = 'Waiting for pickup area to clear.'
                return False
            self.awaiting_clear = False
            if result['bin'] not in self.allowed_bins:
                return False
            if self.arm.state != 'READY' or self.pending_sort is not None:
                self.status = 'New item detected. Waiting for the arm to finish.'
                return False
            self._begin_sort(result['label'], result['bin'], result['confidence'])
            accepted = self.arm.sort(result['bin'])
            if accepted:
                self.awaiting_clear = True
                if self.continuous_sorting:
                    self.last_sorted_signature = signature
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
                        bins=list(self.allowed_bins),
                        recent_sorts=[dict(row) for row in self.recent_sorts],
                        simulation=self.simulation, mock_camera=self.mock_camera,
                        mock_available=self.mock_available, camera_source=self.camera_source,
                        camera_url=self.camera_url, camera_index=self.camera_index,
                        camera_device_id=self.camera_device_id,
                        camera_error=self.camera_error,
                        vision_ready=self.vision_ready, sorting_enabled=self.enabled,
                        status=self.status, arm_detail=self.arm.detail,
                        awaiting_clear=self.awaiting_clear)
