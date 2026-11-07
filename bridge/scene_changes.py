"""Keep live recognition responsive without repeatedly classifying a still scene."""
import hashlib


def scene_signature(frame):
    data, mime = frame[:2]
    if mime == 'image/jpeg':
        try:
            import cv2
            import numpy as np
            image = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
            if image is not None:
                return cv2.resize(image, (32, 32), interpolation=cv2.INTER_AREA)
        except ImportError:
            pass
    return hashlib.blake2s(data, digest_size=16).digest()


def changed(current, previous):
    if isinstance(current, bytes) != isinstance(previous, bytes):
        return True
    if isinstance(current, bytes):
        return current != previous
    import numpy as np
    return float(np.abs(current.astype(np.int16) - previous.astype(np.int16)).mean()) >= 8


class SceneCadence:
    def __init__(self, *, minimum_interval=1.0, heartbeat_interval=12.0, empty_interval=3.0):
        self.minimum_interval = minimum_interval
        self.heartbeat_interval = heartbeat_interval
        self.empty_interval = empty_interval
        self.last_signature = None
        self.last_request_at = None
        self.retry_when_ready = False
        self.verify_empty = False

    def due(self, signature, now, *, ready_to_sort):
        if self.last_request_at is None:
            return True
        if now - self.last_request_at < self.minimum_interval:
            return False
        return (changed(signature, self.last_signature)
                or now - self.last_request_at >= (self.empty_interval if self.verify_empty else self.heartbeat_interval)
                or self.retry_when_ready and ready_to_sort)

    def sent(self, signature, now, *, waiting_for_arm=False, verify_empty=False):
        self.last_signature = signature
        self.last_request_at = now
        self.retry_when_ready = waiting_for_arm
        self.verify_empty = verify_empty
