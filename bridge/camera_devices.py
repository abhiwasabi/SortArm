"""Named webcam discovery, including macOS Continuity Camera."""
import sys


def enumerate_camera_devices():
    if sys.platform == 'darwin':
        from Foundation import NSBundle
        # Opt in for this Python process only; do not modify Python's global Info.plist.
        info = NSBundle.mainBundle().infoDictionary()
        if info is not None and hasattr(info, 'setObject_forKey_'):
            info.setObject_forKey_(True, 'NSCameraUseContinuityCameraDeviceType')
    from cv2_enumerate_cameras import enumerate_cameras
    return enumerate_cameras()
