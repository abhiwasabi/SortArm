import sys
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from bridge.adapters import Camera

class NamedCameraTests(unittest.TestCase):
    def test_uses_selected_device_not_first_phone(self):
        cv = SimpleNamespace(VideoCapture=Mock())
        cv.VideoCapture.return_value.isOpened.return_value = True
        devices = [SimpleNamespace(path='phone', index=1200, backend=0),
                   SimpleNamespace(path='macbook', index=1201, backend=0)]
        enum = SimpleNamespace(enumerate_cameras=lambda: devices)
        with patch.dict(sys.modules, {'cv2': cv, 'cv2_enumerate_cameras': enum}):
            Camera(device_id='macbook')
        cv.VideoCapture.assert_called_once_with(1201, 0)

    def test_missing_selected_camera_never_falls_back_to_phone(self):
        cv = SimpleNamespace(VideoCapture=Mock())
        enum = SimpleNamespace(enumerate_cameras=lambda: [SimpleNamespace(path='phone')])
        with patch.dict(sys.modules, {'cv2': cv, 'cv2_enumerate_cameras': enum}):
            with self.assertRaises(RuntimeError):
                Camera(device_id='macbook')
        cv.VideoCapture.assert_not_called()
