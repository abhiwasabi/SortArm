"""Bench console for the arm, speaking the team's newline text protocol.

    python -m arm.cli --mock          # no hardware
    python -m arm.cli --port COM5     # real SO-101 (or set ARM_PORT)

stdin:  HOME | SORT <BIN> | STOP                   (one per line; QUIT to exit)
        BIN is PLASTIC or METAL (arm/config.py BINS)
stdout: READY | BUSY | DONE | ERROR               (one per state change; nothing else)
stderr: human-readable notes, every line starting with "#", including why a command was rejected.

While idle the console checks the arm once a second, so a lost connection prints ERROR (note:
"# connection lost: ...") without waiting for a command. The bridge (#16) can run this as a
subprocess, or import ArmController directly; only the in-process form exposes `connected` and
`last_done` as values.
"""

from __future__ import annotations

import argparse
import logging
import sys
import threading

from .config import BINS, ArmConfig
from .controller import ArmController, ArmState
from .poses import PosesError


def note(text: str) -> None:
    """Print a human note on stderr; every line starts with "#"."""
    for line in str(text).strip().splitlines() or [""]:
        if line.strip():
            print(f"# {line.strip()}", file=sys.stderr, flush=True)


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
    if args.speed is not None:
        config.speed_deg_s = args.speed
    return ArmController.from_config(config, mock=args.mock)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m arm.cli", description=__doc__.splitlines()[0])
    parser.add_argument("--mock", action="store_true", help="simulate the arm (no hardware)")
    parser.add_argument("--port", help="serial port of the bus-servo board (default: $ARM_PORT)")
    parser.add_argument("--speed", type=float, help="body joint speed in deg/s (default 20)")
    args = parser.parse_args(argv)
    # Library logs (including multi-line tracebacks) go to stderr as "#" notes, keeping stdout for
    # state words only.
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(NoteFormatter())
    logging.basicConfig(level=logging.WARNING, handlers=[handler], force=True)

    try:
        arm = build(args)
    except (PosesError, ValueError, OSError, RuntimeError) as exc:  # OSError covers a wrong or busy port
        note(str(exc).strip())
        return 2

    def report(state: ArmState, detail: str | None) -> None:
        print(state.value, flush=True)
        if detail and state is ArmState.ERROR:
            note(detail)

    arm.on_state(report)
    report(arm.state, arm.detail)
    note("commands: HOME, " + ", ".join(f"SORT {b}" for b in BINS) + ", STOP, QUIT")
    stop_polling = start_polling(arm)

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
        stop_polling.set()
        note("closing; on the real arm this turns torque off, so support the arm or leave it at rest")
        arm.close()
    return 0


class NoteFormatter(logging.Formatter):
    """Prefix every line of a log record with "#", tracebacks and dict dumps included."""

    def format(self, record: logging.LogRecord) -> str:
        text = super().format(record)
        return "\n".join(f"# {line}" for line in text.splitlines() if line.strip()) or "#"


def start_polling(arm: ArmController, interval_s: float = 1.0) -> threading.Event:
    """Check the arm's connection every `interval_s` while idle, like the in-process bridge does.

    A lost connection then shows up as ERROR (with a "# connection lost" note) without waiting for
    the next command. Set the returned event to stop.
    """
    stop = threading.Event()

    def loop() -> None:
        while not stop.wait(interval_s):
            arm.poll()

    threading.Thread(target=loop, name="arm-poll", daemon=True).start()
    return stop


if __name__ == "__main__":
    sys.exit(main())
