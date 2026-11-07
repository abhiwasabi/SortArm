"""Run `python -m bridge --mock` for a complete local simulation."""
import argparse
from pathlib import Path
import threading

from .adapters import Camera, GeminiPredictor, MockSource, prediction_error_message
from .core import Bridge
from .server import make_server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mock', action='store_true', help='Simulate both camera and arm; no cloud calls')
    parser.add_argument('--mock-arm', action='store_true', help='Real camera/Gemini, simulated arm')
    parser.add_argument('--port', type=int, default=4173)
    parser.add_argument('--camera', type=int, default=0)
    args = parser.parse_args()
    try:
        from arm import BINS, ArmConfig, ArmController, MockBackend, So101Backend, demo_poses, load_poses
    except ImportError:
        parser.error('arm/ dependency missing. Integrate Paul\'s #20 branch first; see bridge/README.md.')
    web = Path(__file__).resolve().parents[1] / 'web'
    mock_arm = args.mock or args.mock_arm
    config = ArmConfig.from_env()
    if mock_arm:
        config.speed_deg_s = 90
        config.gripper_speed = 300
    # Validate cloud config before opening any physical device.
    predictor = None if args.mock else GeminiPredictor()
    source = MockSource(web) if args.mock else Camera(args.camera)
    try:
        arm = ArmController.from_config(config, mock=mock_arm)
    except Exception:
        if hasattr(source, 'close'):
            source.close()
        raise
    bridge = Bridge(arm, simulation=mock_arm, mock_camera=args.mock, allowed_bins=BINS,
                    prediction_max_age=30 if mock_arm else 8)
    shutdown = threading.Event()

    def capture_loop():
        while not shutdown.is_set():
            try:
                bridge.camera_frame(source.capture())
            except Exception:
                bridge.camera_lost()
            shutdown.wait(.25)

    def prediction_loop():
        while not shutdown.is_set():
            ticket = bridge.begin_prediction()
            if ticket:
                try:
                    result = (source if args.mock else predictor).predict(ticket[3])
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
    server = make_server(bridge, web, args.port, source if args.mock else None)
    for worker in workers:
        worker.start()
    print(f'Dashboard: http://127.0.0.1:{server.server_port}', flush=True)
    print('SIMULATED ARM' if mock_arm else 'PHYSICAL ARM: verify calibration, taught poses, and power cutoff.', flush=True)
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
        if hasattr(source, 'close'):
            source.close()


if __name__ == '__main__':
    main()
