"""Run `python -m bridge --mock` for a complete local simulation."""
import argparse
import os
from pathlib import Path
import threading

from .adapters import Camera, GeminiPredictor, prediction_error_message
from .camera_sources import CameraSourceManager, Esp32SnapshotSource, validate_esp32_url
from .core import Bridge
from .server import make_server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mock', action='store_true', help='Simulate both camera and arm; no cloud calls')
    parser.add_argument('--mock-arm', action='store_true', help='Real camera/Gemini, simulated arm')
    parser.add_argument('--port', type=int, default=4173)
    parser.add_argument('--camera', type=int, default=0)
    parser.add_argument('--esp32-url', help='Local HTTP JPEG snapshot URL, e.g. http://192.168.1.50/capture')
    args = parser.parse_args()
    try:
        from arm import BINS, ArmConfig, ArmController, MockBackend, So101Backend, demo_poses, load_poses
    except ImportError:
        parser.error('arm/ dependency missing. Integrate Paul\'s #20 branch first; see bridge/README.md.')
    web = Path(__file__).resolve().parents[1] / 'web'
    mock_arm = args.mock or args.mock_arm
    esp32_url = validate_esp32_url(args.esp32_url) if args.esp32_url else None
    config = ArmConfig.from_env()
    if mock_arm:
        config.speed_deg_s = 90
        config.gripper_speed = 300
    # Physical motion still requires complete cloud configuration before the arm connects.
    has_gemini = bool(os.environ.get('GEMINI_API_KEY') and os.environ.get('GEMINI_MODEL'))
    predictor = GeminiPredictor() if has_gemini or not mock_arm else None
    # Keep the prior physical-mode order: verify the camera before connecting the arm.
    initial_source = None
    if not mock_arm:
        initial_source = Esp32SnapshotSource(esp32_url) if esp32_url else Camera(args.camera)
        if esp32_url:
            initial_source.capture()
    try:
        arm = ArmController.from_config(config, mock=mock_arm)
    except Exception:
        if hasattr(initial_source, 'close'):
            initial_source.close()
        raise
    initial_name = 'esp32' if esp32_url and not mock_arm else 'mock' if args.mock else 'usb'
    bridge = Bridge(arm, simulation=mock_arm, mock_camera=args.mock, mock_available=args.mock,
                    camera_source=initial_name, camera_url=esp32_url if not mock_arm else None,
                    camera_index=args.camera,
                    vision_ready=args.mock or predictor is not None, allowed_bins=BINS,
                    prediction_max_age=30 if mock_arm else 8)
    camera = CameraSourceManager(bridge, web, allow_mock=args.mock, predictor=predictor, index=args.camera,
                                 initial_source=initial_source, initial_name=initial_name,
                                 initial_url=esp32_url if not mock_arm else None)
    if esp32_url and mock_arm:
        camera.select('esp32', url=esp32_url)
    shutdown = threading.Event()

    def capture_loop():
        while not shutdown.is_set():
            camera.capture_once()
            shutdown.wait(camera.capture_interval())

    def prediction_loop():
        while not shutdown.is_set():
            ticket = bridge.begin_prediction()
            delay = 0.1
            if ticket:
                try:
                    result = camera.predict(ticket)
                    if result is not None:
                        bridge.accept_prediction(ticket, result)
                except Exception as exc:
                    message = prediction_error_message(exc)
                    print(message, flush=True)
                    bridge.prediction_failed(ticket, message)
                    delay = 1.5  # Back off on failures; successful requests need no extra pause.
            shutdown.wait(delay)

    def poll_loop():
        while not shutdown.wait(1):
            try:
                arm.poll()
            except Exception:
                with bridge.lock:
                    bridge.enabled = False
                    bridge.invalidate('Arm polling failed. Check connection and restart.')

    workers = [threading.Thread(target=fn, daemon=True) for fn in (capture_loop, prediction_loop, poll_loop)]
    server = make_server(bridge, web, args.port, camera_manager=camera)
    for worker in workers:
        worker.start()
    print(f'Dashboard: http://127.0.0.1:{server.server_port}', flush=True)
    print('SIMULATED ARM' if mock_arm else 'PHYSICAL ARM: verify calibration, taught poses, and power cutoff.', flush=True)
    if predictor is None:
        print('Gemini is not configured; live cameras can preview, but sorting is disabled.', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        shutdown.set()
        bridge.stop()
        for worker in workers:
            worker.join(timeout=8)
        server.server_close()
        arm.close()
        camera.close()


if __name__ == '__main__':
    main()
