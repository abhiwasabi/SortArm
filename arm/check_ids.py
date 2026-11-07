"""Check which servo IDs answer and whether their models match the SO-101 driver."""

from __future__ import annotations

import argparse
import os
import re
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Literal, Protocol

from .config import JOINTS


class Bus(Protocol):
    def ping(self, motor_id: int) -> int | None:
        """Return the model number, or None if the servo does not answer."""

    def disconnect(self, disable_torque: bool = True) -> None:
        """Close the serial port."""


@dataclass(frozen=True)
class Row:
    id: int
    joint: str
    model: int | None
    status: Literal["OK", "MISSING", "WRONG MODEL", "UNEXPECTED"]


def parse_ids(value: str) -> list[int]:
    """Parse comma-separated IDs and inclusive ranges, excluding broadcast IDs."""
    ids: set[int] = set()
    for part in value.split(","):
        match = re.fullmatch(r"\s*([0-9]+)\s*(?:-\s*([0-9]+)\s*)?", part)
        if match is None:
            raise argparse.ArgumentTypeError("Use IDs or ranges such as 1-6 or 1,2,5.")
        first = int(match[1])
        last = int(match[2]) if match[2] is not None else first
        if not 0 <= first <= last <= 253:
            raise argparse.ArgumentTypeError("Servo IDs must be between 0 and 253, with ranges ascending.")
        ids.update(range(first, last + 1))
    return sorted(ids)


def _joint(motor_id: int) -> str:
    return JOINTS[motor_id - 1] if 1 <= motor_id <= len(JOINTS) else f"id{motor_id}"


def scan(bus: Bus, ids: Iterable[int]) -> dict[int, int | None]:
    """Ping each ID; one failed read must not stop the remaining checks."""
    found: dict[int, int | None] = {}
    for motor_id in ids:
        try:
            found[motor_id] = bus.ping(motor_id)
        except Exception:
            found[motor_id] = None
    return found


def classify(found: dict[int, int | None], expected_ids: Iterable[int],
             expected_model: int = 777) -> list[Row]:
    """Include every expected ID and any additional servo that answered."""
    expected = set(expected_ids)
    answering = {motor_id for motor_id, model in found.items() if model is not None}
    rows: list[Row] = []
    for motor_id in sorted(expected | answering):
        model = found.get(motor_id)
        if motor_id not in expected:
            status = "UNEXPECTED"
        elif model is None:
            status = "MISSING"
        elif model != expected_model:
            status = "WRONG MODEL"
        else:
            status = "OK"
        rows.append(Row(motor_id, _joint(motor_id), model, status))
    return rows


def hints(rows: Sequence[Row]) -> list[str]:
    """Suggest the next bench check from the observed replies."""
    messages: list[str] = []
    answering = {row.id for row in rows if row.model is not None}
    if not answering:
        messages.append(
            "No servos answered. Check the BusLinker communication jumper (Servo + USB), "
            "the 3-pin connector pin order (GND / V+ / signal must match the servo cable; "
            "check with a multimeter), the 12 V supply, the port, and the baud."
        )
    if answering == {1} and sum(row.status != "UNEXPECTED" for row in rows) > 1:
        messages.append(
            "The servos probably still have their factory ID 1. Set IDs one servo at a time with "
            "lerobot-setup-motors --robot.type=so101_follower --robot.port=<port> "
            "(arm/README.md step 2)."
        )
    for row in rows:
        if row.status == "WRONG MODEL":
            messages.append(
                f"servo {row.id} reports model {row.model}; LeRobot expects 777 (STS3215). "
                "Post this number so arm/ can be adapted."
            )
    if rows and all(row.status == "OK" for row in rows):
        messages.append("All servos answered as STS3215-compatible (model 777).")
    return messages


def _open_bus(port: str, ids: list[int], baud: int) -> Bus:
    """Open without a model handshake; release the port if setup fails."""
    from lerobot.motors.motors_bus import Motor, MotorNormMode, SerialMotorsBus

    motors = {
        _joint(motor_id): Motor(motor_id, "sts3215", MotorNormMode.RANGE_M100_100)
        for motor_id in ids
    }
    bus = SerialMotorsBus(port=port, motors=motors)
    try:
        bus.connect(handshake=False)
        if baud != bus.default_baudrate:
            bus.set_baudrate(baud)
    except BaseException:
        bus.disconnect(disable_torque=False)
        raise
    return bus


def _print_table(rows: Sequence[Row]) -> None:
    cells = [("ID", "joint", "model", "status")]
    cells.extend((str(row.id), row.joint, str(row.model) if row.model is not None else "-", row.status)
                 for row in rows)
    widths = [max(len(cell[column]) for cell in cells) for column in range(4)]
    for cell in cells:
        print("  ".join(value.ljust(width) for value, width in zip(cell, widths)).rstrip())


def main(argv: Sequence[str] | None = None,
         open_bus: Callable[[str, list[int], int], Bus] | None = None) -> int:
    """Run the check. An injected factory returns an open bus and cleans up failed opens."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", default=os.environ.get("ARM_PORT"))
    parser.add_argument("--ids", type=parse_ids, default="1-6", help="Expected IDs (default: 1-6).")
    parser.add_argument("--scan", type=parse_ids, metavar="RANGE", help="Ping this range instead of --ids.")
    parser.add_argument("--baud", type=int, default=1_000_000)
    args = parser.parse_args(argv)
    if not args.port:
        print("Set --port or ARM_PORT (find the port with lerobot-find-port).")
        return 2
    if args.baud <= 0:
        parser.error("--baud must be a positive number.")

    ids = args.scan if args.scan is not None else args.ids
    factory = open_bus if open_bus is not None else _open_bus
    try:
        bus = factory(args.port, ids, args.baud)
        try:
            found = scan(bus, ids)
        finally:
            bus.disconnect(disable_torque=False)
    except (OSError, RuntimeError) as exc:  # LeRobot: ConnectionError for the port, RuntimeError for bus setup
        print(
            f"Could not use port {args.port!r}: {str(exc).strip()}; "
            "check the USB cable, the CH341 driver, and that no other program uses the port."
        )
        return 2

    rows = classify(found, args.ids)
    _print_table(rows)
    for message in hints(rows):
        print(message)
    return 0 if all(row.status == "OK" for row in rows if row.id in args.ids) else 1


if __name__ == "__main__":
    raise SystemExit(main())
