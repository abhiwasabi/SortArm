"""Bench console for the arm, speaking the team's newline text protocol.

    python -m arm.cli --mock          # no hardware
    python -m arm.cli --port COM5     # real SO-101 (or set ARM_PORT)

stdin:  HOME | SORT RECYCLE | SORT TRASH | STOP   (one per line; QUIT to exit)
stdout: READY | BUSY | DONE | ERROR               (one per state change; nothing else)
stderr: human-readable notes, starting with "#", including why a command was rejected.

The bridge (#16) can run this as a subprocess, or import ArmController directly.
"""

from __future__ import annotations

import argparse
import sys

from .backend import MockBackend, So101Backend
from .config import ArmConfig
from .controller import ArmController, ArmState
from .poses import PosesError, demo_poses, load


def note(text: str) -> None:
    print(f"# {text}", file=sys.stderr, flush=True)


def handle_line(arm: ArmController, line: str) -> str | None:
    """Apply one protocol line. Returns a rejection reason, or None if the command was accepted."""
    words = line.strip().upper().split()
    if not words:
        return None
    if words == ["HOME"]:
        accepted = arm.home()
    elif words == ["STOP"]:
        accepted = arm.stop()
    elif len(words) == 2 and words[0] == "SORT":
        accepted = arm.sort(words[1])
    else:
        return f"unknown command {line.strip()!r}"
    return None if accepted else arm.last_rejection


def build(args: argparse.Namespace) -> ArmController:
    config = ArmConfig.from_env()
    if args.port:
        config.port = args.port
    if args.speed:
        config.speed_deg_s = args.speed

    poses = load(config.poses_path)
    if args.mock:
        if poses.missing():
            note("poses.json is not fully taught; the mock uses made-up demo poses")
            poses = demo_poses()
        backend = MockBackend(start={**poses.body("home"), "gripper": poses.grip("open")})
    else:
        poses.validate()  # fail before connecting to the arm
        backend = So101Backend(config)
    return ArmController(backend, poses, config)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m arm.cli", description=__doc__.splitlines()[0])
    parser.add_argument("--mock", action="store_true", help="simulate the arm (no hardware)")
    parser.add_argument("--port", help="serial port of the bus-servo board (default: $ARM_PORT)")
    parser.add_argument("--speed", type=float, help="body joint speed in deg/s (default 20)")
    args = parser.parse_args(argv)

    try:
        arm = build(args)
    except (PosesError, ValueError, RuntimeError) as exc:
        note(str(exc))
        return 2

    def report(state: ArmState, detail: str | None) -> None:
        print(state.value, flush=True)
        if detail and state is ArmState.ERROR:
            note(detail)

    arm.on_state(report)
    report(arm.state, arm.detail)
    note("commands: HOME, SORT RECYCLE, SORT TRASH, STOP, QUIT")

    try:
        for line in sys.stdin:
            if line.strip().upper() in ("QUIT", "EXIT"):
                break
            reason = handle_line(arm, line)
            if reason:
                note(f"rejected: {reason}")
    except KeyboardInterrupt:
        pass
    finally:
        note("closing; on the real arm this turns torque off, so support the arm or leave it at rest")
        arm.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
