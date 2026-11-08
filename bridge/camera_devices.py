"""Named webcam discovery, including macOS Continuity Camera.

The dashboard serves each request on a worker thread, while macOS delivers
camera connect and disconnect notifications only to the main-thread run loop.
The accept loop has to pump that run loop or a phone plugged in after startup
stays invisible until the process is restarted.
"""
import sys
import threading
import time

_refresh = threading.Event()


def enable_continuity_cameras():
    """Opt this process into Continuity Camera without editing Python's Info.plist."""
    if sys.platform != 'darwin':
        return
    from Foundation import NSBundle
    info = NSBundle.mainBundle().infoDictionary()
    if info is not None and hasattr(info, 'setObject_forKey_'):
        info.setObject_forKey_(True, 'NSCameraUseContinuityCameraDeviceType')


def _on_main_thread():
    if sys.platform != 'darwin':
        return True
    from Foundation import NSThread
    return bool(NSThread.isMainThread())


def request_camera_refresh(timeout=0.6):
    """Make the main thread deliver pending camera notifications before a listing."""
    enable_continuity_cameras()
    if sys.platform != 'darwin':
        return
    if _on_main_thread():
        pump_camera_notifications()
        return
    _refresh.set()
    deadline = time.monotonic() + timeout
    while _refresh.is_set() and time.monotonic() < deadline:
        time.sleep(0.01)


def pump_camera_notifications():
    """Run from the server main thread. A zero wait drains events already queued."""
    if sys.platform != 'darwin':
        _refresh.clear()
        return
    from Foundation import NSDate, NSRunLoop
    seconds = 0.2 if _refresh.is_set() else 0
    NSRunLoop.currentRunLoop().runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(seconds))
    _refresh.clear()


def enumerate_camera_devices():
    request_camera_refresh()
    from cv2_enumerate_cameras import enumerate_cameras
    return enumerate_cameras()
