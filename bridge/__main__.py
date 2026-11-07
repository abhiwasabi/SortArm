"""Run `python -m bridge --mock` for a complete local simulation."""
import argparse
import os
from pathlib import Path
import sys
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
        from arm import BINS, ArmConfig, ArmController, PosesError
    except ImportError:
        parser.error('arm/ dependency missing. Integrate Paul\'s #20 branch first; see bridge/README.md.')
    web = Path(__file__).resolve().parents[1] / 'web'
    mock_arm = args.mock or args.mock_arm
    esp32_url = validate_esp32_url(args.esp32_url) if args.esp32_url else None
    # Physical motion still requires complete cloud configuration before the arm connects.
    has_gemini = bool(os.environ.get('GEMINI_API_KEY') and os.environ.get('GEMINI_MODEL'))
    try:
        predictor = GeminiPredictor() if has_gemini or not mock_arm else None
    except ValueError as exc:
        print('Live vision is not configured: ' + str(exc) + '. Next step: set GEMINI_API_KEY and '
              'GEMINI_MODEL, or start with --mock-arm (real camera, simulated arm) or --mock.', file=sys.stderr)
        raise SystemExit(2) from None
    # Keep the prior physical-mode order: verify the camera before connecting the arm.
    initial_source = None
    if not mock_arm:
        initial_source = Esp32SnapshotSource(esp32_url) if esp32_url else Camera(args.camera)
        if esp32_url:
            initial_source.capture()
    try:
        config = ArmConfig.from_env()
        if mock_arm:
            config.speed_deg_s = 90
            config.gripper_speed = 300
        arm = ArmController.from_config(config, mock=mock_arm)
    except (PosesError, ValueError, OSError, RuntimeError) as exc:
        if hasattr(initial_source, 'close'):
            initial_source.close()
        message = ' '.join(str(exc).split())
        if isinstance(exc, PosesError) or 'pose' in message.lower():
            next_step = 'Teach the poses: python -m arm.teach'
        elif 'calibrat' in message.lower():
            next_step = 'Run lerobot-calibrate; see arm/README.md.'
        elif isinstance(exc, OSError) or 'port' in message.lower():
            next_step = 'Set ARM_PORT to the verified serial port and check USB/power.'
        elif isinstance(exc, ValueError):
            next_step = 'Check the ARM_* settings; see arm/README.md.'
        else:
            next_step = 'Check arm wiring, power, and LeRobot setup; see arm/README.md.'
        print('Arm startup failed: ' + message + ' Next step: ' + next_step, file=sys.stderr)
        raise SystemExit(2) from None
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
            if ticket:
                try:
                    result = camera.predict(ticket)
                    if result is not None:
                        bridge.accept_prediction(ticket, result)
                except Exception as exc:
                    message = prediction_error_message(exc)
                    print(message, flush=True)
                    bridge.prediction_failed(ticket, message)
            shutdown.wait(1.5)

    def poll_loop():
        while not shutdown.wait(1):
            try:
                arm.poll()
            except Exception:
                with bridge.lock:
                    bridge.enabled = False
                    bridge.invalidate('Arm polling failed. Check connection and restart.')

    workers = [threading.Thread(target=fn, daemon=True) for fn in (capture_loop, prediction_loop, poll_loop)]
    server = None
    try:
        server = make_server(bridge, web, args.port, camera_manager=camera)
        for worker in workers:
            worker.start()
        print(f'Dashboard: http://127.0.0.1:{server.server_port}', flush=True)
        print('SIMULATED ARM' if mock_arm else 'PHYSICAL ARM: verify calibration, taught poses, and power cutoff.', flush=True)
        if predictor is None:
            print('Gemini is not configured; live cameras can preview, but sorting is disabled.', flush=True)
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    except OSError as exc:
        message = ' '.join(str(exc).split())
        print(f'Dashboard server failed on port {args.port}: {message} Choose an unused --port.', file=sys.stderr)
        raise SystemExit(2) from None
    finally:
        shutdown.set()
        try:
            bridge.stop()
        finally:
            try:
                for worker in workers:
                    if worker.ident is not None:
                        worker.join(timeout=8)
                if server is not None:
                    server.server_close()
            finally:
                try:
                    arm.close()
                finally:
                    camera.close()


if __name__ == '__main__':
    main()
