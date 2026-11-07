"""Teach mode: move the arm by hand and save named poses to arm/poses.json.

    python -m arm.teach --port COM5   (or set ARM_PORT)

Torque is turned off so the arm can be moved by hand. HOLD THE ARM when torque goes off:
the shoulder and elbow drop under gravity.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from .backend import Backend, MockBackend, So101Backend
from .config import BODY_JOINTS, GRIPPER, ArmConfig
from .poses import GRIPPER_KEYS, REQUIRED_POSES, Poses, load, save

HELP = """\
Commands (type and press Enter):
  <Enter>            show the current joint readings
  home | above_pick | pick | above_recycle | above_trash
                     save the arm's current position under that name
  open | closed      save the current gripper opening as the open / closed value
  list               show which poses are saved and which are missing
  t                  toggle torque (on = arm holds still; off = move it by hand)
  help               show this help
  q | quit           quit (torque turns off: support the arm or leave it at rest)
Any other simple name (letters, digits, _) saves a custom pose."""

NAME = re.compile(r"^[a-z][a-z0-9_]*$")
QUIT = {"q", "quit", "exit"}
SHOW_HELP = {"help", "h", "?"}
# Read failures from the bus (LeRobot raises OSError/ConnectionError or RuntimeError).
READ_ERRORS = (OSError, RuntimeError)


def fmt(reading: dict[str, float]) -> str:
    return "  ".join(f"{joint}={reading[joint]:7.1f}" for joint in (*BODY_JOINTS, GRIPPER))


class TeachSession:
    """The teach-mode commands, separated from the console so they can be tested."""

    def __init__(self, backend: Backend, poses: Poses, path: Path, torque: bool = False):
        self.backend = backend
        self.poses = poses
        self.path = path
        self.torque = torque

    def handle(self, raw: str) -> tuple[str, bool]:
        """Run one command. Returns (message to print, keep going)."""
        cmd = raw.strip().lower()
        if cmd in QUIT:
            return "", False
        if cmd in SHOW_HELP:
            return HELP, True
        if cmd == "list":
            return self._list(), True
        if cmd == "t":
            try:
                self.backend.set_torque(not self.torque)
            except READ_ERRORS as exc:
                return f"Could not change torque ({exc}). Check the cable and power, then try again.", True
            self.torque = not self.torque
            return ("Torque ON: the arm holds its position." if self.torque else "Torque OFF: hold the arm."), True
        if not (cmd == "" or cmd in GRIPPER_KEYS or NAME.match(cmd)):
            return "Unknown command. Type 'help' for the list; 'q' quits.", True

        try:
            reading = {joint: round(value, 1) for joint, value in self.backend.read().items()}
        except READ_ERRORS as exc:
            return f"Could not read the arm ({exc}). Check the cable and power, then try again.", True
        if cmd == "":
            return fmt(reading), True
        if cmd in GRIPPER_KEYS:
            self.poses.gripper[cmd] = reading[GRIPPER]
            saved = f"Saved gripper {cmd} = {reading[GRIPPER]}"
        else:
            self.poses.poses[cmd] = {joint: reading[joint] for joint in BODY_JOINTS}
            tag = "" if cmd in REQUIRED_POSES else " (custom pose)"
            saved = f"Saved {cmd}{tag}: {fmt(reading)}"
        try:
            save(self.poses, self.path)
        except OSError as exc:  # e.g. OneDrive or an editor holding the file
            return (f"Recorded {cmd!r} but could not write {self.path} ({exc}). "
                    "Close whatever holds the file, then save the pose again."), True
        return saved, True

    def _list(self) -> str:
        lines = [f"  {name:14} {'saved' if self.poses.has(name) else 'MISSING'}" for name in REQUIRED_POSES]
        for key in GRIPPER_KEYS:
            value = self.poses.gripper.get(key) if self.poses.has_grip(key) else "MISSING"
            lines.append(f"  gripper {key:6} {value}")
        extra = sorted(set(self.poses.poses) - set(REQUIRED_POSES))
        if extra:
            lines.append("  custom: " + ", ".join(extra))
        return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m arm.teach", description="Record arm poses by hand.")
    parser.add_argument("--port", help="serial port of the bus-servo board (default: $ARM_PORT)")
    parser.add_argument("--mock", action="store_true", help="simulated arm, for trying the commands")
    args = parser.parse_args(argv)

    config = ArmConfig.from_env()
    if args.port:
        config.port = args.port
    try:
        poses = load(config.poses_path)  # before opening the port: a broken file must not leave it open
    except (OSError, ValueError) as exc:  # ValueError covers invalid JSON (e.g. merge-conflict markers)
        print(f"error: cannot read {config.poses_path}: {exc}", file=sys.stderr)
        return 2
    try:
        backend: Backend = MockBackend() if args.mock else So101Backend(config)
    except (ValueError, OSError, RuntimeError) as exc:  # OSError covers a wrong or busy port
        print(f"error: {str(exc).strip()}", file=sys.stderr)
        return 2

    session = TeachSession(backend, poses, config.poses_path, torque=True)
    try:
        print(HELP)
        print(f"\nPoses file: {config.poses_path}")
        input("\nHold the arm, then press Enter to turn torque OFF... ")
        message, _ = session.handle("t")
        print(message)
        while True:
            message, keep_going = session.handle(input("teach> "))
            if message:
                print(message)
            if not keep_going:
                break
    except (KeyboardInterrupt, EOFError):
        print()
    finally:
        print("Closing: torque turns off.")
        backend.close()

    missing = session.poses.missing()
    print("All required poses saved." if not missing else "Still missing: " + ", ".join(missing))
    return 0


if __name__ == "__main__":
    sys.exit(main())
