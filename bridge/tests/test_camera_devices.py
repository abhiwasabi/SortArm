import sys
import threading
import types
import unittest
from unittest.mock import patch

from bridge import camera_devices


class CameraRefreshTests(unittest.TestCase):
    def tearDown(self):
        camera_devices._refresh.clear()

    def test_worker_listing_waits_until_the_main_thread_pumps(self):
        released = threading.Event()

        def pump_once():
            released.set()
            camera_devices._refresh.clear()

        def worker():
            with patch.object(camera_devices, '_on_main_thread', return_value=False), \
                    patch.object(camera_devices, 'enable_continuity_cameras'):
                camera_devices.request_camera_refresh(timeout=1)

        thread = threading.Thread(target=worker)
        thread.start()
        self.assertTrue(camera_devices._refresh.wait(1))
        pump_once()
        thread.join(1)
        self.assertFalse(thread.is_alive())
        self.assertTrue(released.is_set())

    def test_enumerate_refreshes_before_reading_devices(self):
        calls = []
        module = types.ModuleType('cv2_enumerate_cameras')
        module.enumerate_cameras = lambda: calls.append('list') or ['phone']
        with patch.object(camera_devices, 'request_camera_refresh', side_effect=lambda: calls.append('refresh')), \
                patch.dict(sys.modules, {'cv2_enumerate_cameras': module}):
            self.assertEqual(camera_devices.enumerate_camera_devices(), ['phone'])
        self.assertEqual(calls, ['refresh', 'list'])
