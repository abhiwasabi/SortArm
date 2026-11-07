"""Teach mode: move the arm by hand and save named poses to arm/poses.json.

    python -m arm.teach --port COM5   (or set ARM_PORT)

Torque is turned off so the arm can be moved by hand. HOLD THE ARM when torque goes off:
the shoulder and elbow drop under gravity.
"""

from __future__ import annotations

import argparse
import re
import sys

from .backend import MockBackend, So101Backend
from .config import BODY_JOINTS, GRIPPER, ArmConfig
from .poses import GRIPPER_KEYS, REQUIRED_POSES, load, save

HELP = """\
Commands (type and press Enter):
  <Enter>            show the current joint readings
  home | above_pick | pick | above_recycle | above_trash
                     save the arm's current position under that name
  open | closed      save the current gripper opening as the open / closed value
  list               show which poses are saved and which are missing
  t                  toggle torque (on = arm holds still; off = move it by hand)
  q                  quit (torque turns off: support the arm or leave it at rest)
Any other simple name (letters, digits, _) saves a custom pose."""

NAME = re.compile(r"^[a-z][a-z0-9_]*$")


def fmt(reading: dict[str, float]) -> str:
    return "  ".join(f"{joint}={reading[joint]:7.1f}" for joint in (*BODY_JOINTS, GRIPPER))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m arm.teach", description="Record arm poses by hand.")
    parser.add_argument("--port", help="serial port of the Waveshare board (default: $ARM_PORT)")
    parser.add_argument("--mock", action="store_true", help="simulated arm, for trying the commands")
    args = parser.parse_args(argv)

    config = ArmConfig.from_env()
    if args.port:
        config.port = args.port
    try:
        backend = MockBackend() if args.mock else So101Backend(config)
    except (ValueError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    poses = load(config.poses_path)
    print(HELP)
    print(f"\nPoses file: {config.poses_path}")
    input("\nHold the arm, then press Enter to turn torque OFF... ")
    backend.set_torque(False)
    torque = False
    print("Torque OFF. Move the arm by hand.")

    try:
        while True:
            cmd = input("teach> ").strip().lower()
            reading = {joint: round(value, 1) for joint, value in backend.read().items()}
            if cmd == "":
                print(fmt(reading))
            elif cmd == "q":
                break
            elif cmd == "t":
                torque = not torque
                backend.set_torque(torque)
                print("Torque ON: the arm holds its position." if torque else "Torque OFF: hold the arm.")
            elif cmd == "list":
                for name in REQUIRED_POSES:
                    print(f"  {name:14} {'saved' if poses.poses.get(name) else 'MISSING'}")
                for key in GRIPPER_KEYS:
                    value = poses.gripper.get(key)
                    print(f"  gripper {key:6} {value if value is not None else 'MISSING'}")
                extra = sorted(set(poses.poses) - set(REQUIRED_POSES))
                if extra:
                    print("  custom:", ", ".join(extra))
            elif cmd in GRIPPER_KEYS:
                poses.gripper[cmd] = reading[GRIPPER]
                save(poses, config.poses_path)
                print(f"Saved gripper {cmd} = {reading[GRIPPER]}")
            elif NAME.match(cmd):
                poses.poses[cmd] = {joint: reading[joint] for joint in BODY_JOINTS}
                save(poses, config.poses_path)
                tag = "" if cmd in REQUIRED_POSES else " (custom pose)"
                print(f"Saved {cmd}{tag}: {fmt(reading)}")
            else:
                print("Unknown command. Press Enter on an empty line to see readings; 'q' quits.")
    except (KeyboardInterrupt, EOFError):
        print()
    finally:
        print("Closing: torque turns off.")
        backend.close()

    missing = poses.missing()
    print("All required poses saved." if not missing else "Still missing: " + ", ".join(missing))
    return 0


if __name__ == "__main__":
    sys.exit(main())
