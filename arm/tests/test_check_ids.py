"""Servo checks use fake buses and never open a serial port."""

from __future__ import annotations

import argparse
import sys
from types import ModuleType, SimpleNamespace

import pytest

from arm import check_ids
from arm.config import JOINTS


class FakeBus:
    default_baudrate = 1_000_000

    def __init__(self, replies: dict[int, int | None | BaseException],
                 connect_error: BaseException | None = None,
                 baud_error: BaseException | None = None):
        self.replies = replies
        self.connect_error = connect_error
        self.baud_error = baud_error
        self.pings: list[int] = []
        self.handshakes: list[bool] = []
        self.bauds: list[int] = []
        self.disconnects: list[bool] = []

    def connect(self, handshake: bool = True) -> None:
        self.handshakes.append(handshake)
        if self.connect_error:
            raise self.connect_error

    def set_baudrate(self, baud: int) -> None:
        self.bauds.append(baud)
        if self.baud_error:
            raise self.baud_error

    def ping(self, motor_id: int) -> int | None:
        self.pings.append(motor_id)
        reply = self.replies.get(motor_id)
        if isinstance(reply, BaseException):
            raise reply
        return reply

    def disconnect(self, disable_torque: bool = True) -> None:
        self.disconnects.append(disable_torque)


def test_all_ok() -> None:
    rows = check_ids.classify(dict.fromkeys(range(1, 7), 777), range(1, 7))
    assert [row.status for row in rows] == ["OK"] * 6
    assert [row.joint for row in rows] == list(JOINTS)
    assert check_ids.hints(rows) == ["All servos answered as STS3215-compatible (model 777)."]


def test_missing() -> None:
    rows = check_ids.classify({1: 777, 2: None, 3: 777}, range(1, 5))
    assert [row.status for row in rows] == ["OK", "MISSING", "OK", "MISSING"]
    assert rows[1].model is None
    assert check_ids.hints(rows) == []


def test_wrong_model() -> None:
    rows = check_ids.classify({1: 123, 2: 777}, [1, 2])
    assert [row.status for row in rows] == ["WRONG MODEL", "OK"]
    assert check_ids.hints(rows) == [
        "servo 1 reports model 123; LeRobot expects 777 (STS3215). "
        "Post this number so arm/ can be adapted."
    ]
    assert check_ids.classify({1: 123}, [1], expected_model=123)[0].status == "OK"


def test_unexpected_in_scan() -> None:
    rows = check_ids.classify({0: None, 1: 777, 7: 123, 8: None}, [1, 2])
    assert [(row.id, row.status) for row in rows] == [(1, "OK"), (2, "MISSING"), (7, "UNEXPECTED")]
    assert rows[-1].joint == "id7"
    assert rows[-1].model == 123
    assert check_ids.hints(rows) == []


def test_factory_id_one() -> None:
    rows = check_ids.classify({1: 777}, range(1, 7))
    assert [row.status for row in rows] == ["OK"] + ["MISSING"] * 5
    message, = check_ids.hints(rows)
    assert "factory ID 1" in message
    assert "one servo at a time" in message
    assert "lerobot-setup-motors --robot.type=so101_follower --robot.port=<port>" in message
    assert "arm/README.md step 2" in message


def test_no_answers() -> None:
    rows = check_ids.classify({1: None, 2: None}, [1, 2])
    message, = check_ids.hints(rows)
    for text in ("BusLinker", "Servo + USB", "GND / V+ / signal", "multimeter", "12 V", "port", "baud"):
        assert text in message


@pytest.mark.parametrize(("value", "expected"), [
    ("1-6", [1, 2, 3, 4, 5, 6]),
    ("1,2,5", [1, 2, 5]),
    ("0-2, 2, 7", [0, 1, 2, 7]),
    ("253", [253]),
])
def test_parse_ids(value: str, expected: list[int]) -> None:
    assert check_ids.parse_ids(value) == expected


@pytest.mark.parametrize("value", ["", "bad", "6-1", "-1", "254", "1,,2", "1-2-3", "1,", "1.5"])
def test_parse_ids_bad(value: str) -> None:
    with pytest.raises(argparse.ArgumentTypeError):
        check_ids.parse_ids(value)


def test_scan_continues_after_exception() -> None:
    bus = FakeBus({1: 777, 2: RuntimeError("timeout"), 3: 123})
    assert check_ids.scan(bus, [1, 2, 3, 4]) == {1: 777, 2: None, 3: 123, 4: None}
    assert bus.pings == [1, 2, 3, 4]


def test_main_missing_port(monkeypatch, capsys) -> None:
    monkeypatch.delenv("ARM_PORT", raising=False)

    def factory(*args):
        pytest.fail("A missing port must not open a bus.")

    assert check_ids.main([], open_bus=factory) == 2
    assert capsys.readouterr().out.strip() == "Set --port or ARM_PORT (find the port with lerobot-find-port)."


@pytest.mark.parametrize("error", [ConnectionError("busy"), OSError("unplugged")])
def test_main_open_failure(error: OSError, capsys) -> None:
    def factory(port, ids, baud):
        raise error

    assert check_ids.main(["--port", "COM9"], open_bus=factory) == 2
    message = capsys.readouterr().out
    assert "COM9" in message
    assert "check the USB cable, the CH341 driver, and that no other program uses the port" in message


@pytest.mark.parametrize(("replies", "exit_code"), [
    (dict.fromkeys(range(1, 7), 777), 0),
    (dict.fromkeys(range(1, 6), 777), 1),
    ({1: 123}, 1),
    ({}, 1),
    ({1: ConnectionError("lost")}, 1),
])
def test_main_results_disconnect(replies, exit_code: int, capsys) -> None:
    bus = FakeBus(replies)

    def factory(port, ids, baud):
        assert (port, ids, baud) == ("COM9", list(range(1, 7)), 1_000_000)
        return bus

    assert check_ids.main(["--port", "COM9"], open_bus=factory) == exit_code
    assert bus.disconnects == [False]
    assert bus.pings == list(range(1, 7))
    output = capsys.readouterr().out
    assert output.splitlines()[0].split() == ["ID", "joint", "model", "status"]
    assert "shoulder_pan" in output


def test_main_scan_and_environment(monkeypatch, capsys) -> None:
    monkeypatch.setenv("ARM_PORT", "COM8")
    bus = FakeBus({1: 777, 2: 777, 7: 777})

    def factory(port, ids, baud):
        assert (port, ids, baud) == ("COM8", list(range(9)), 115_200)
        return bus

    assert check_ids.main(["--ids", "1,2", "--scan", "0-8", "--baud", "115200"], factory) == 0
    assert bus.pings == list(range(9))
    assert bus.disconnects == [False]
    assert "UNEXPECTED" in capsys.readouterr().out


def test_main_interrupt_disconnects() -> None:
    bus = FakeBus({1: KeyboardInterrupt()})
    with pytest.raises(KeyboardInterrupt):
        check_ids.main(["--port", "COM9"], lambda port, ids, baud: bus)
    assert bus.disconnects == [False]


@pytest.mark.parametrize(("baud", "failure"), [
    (1_000_000, None),
    (115_200, None),
    (1_000_000, "connect"),
    (115_200, "baud"),
])
def test_real_factory_lifecycle_with_fake_lerobot(monkeypatch, baud: int, failure: str | None) -> None:
    bus = FakeBus({}, connect_error=ConnectionError("busy") if failure == "connect" else None,
                  baud_error=OSError("lost") if failure == "baud" else None)
    module = ModuleType("lerobot.motors.motors_bus")

    def make_bus(*, port, motors):
        assert port == "COM9"
        assert list(motors) == [*JOINTS, "id0", "id7"]
        for motor_id, motor in zip([1, 2, 3, 4, 5, 6, 0, 7], motors.values()):
            assert motor == (motor_id, "sts3215", "test_norm")
        return bus

    module.SerialMotorsBus = make_bus
    module.Motor = lambda *args: args
    module.MotorNormMode = SimpleNamespace(RANGE_M100_100="test_norm")
    monkeypatch.setitem(sys.modules, "lerobot", ModuleType("lerobot"))
    monkeypatch.setitem(sys.modules, "lerobot.motors", ModuleType("lerobot.motors"))
    monkeypatch.setitem(sys.modules, "lerobot.motors.motors_bus", module)
    ids = [1, 2, 3, 4, 5, 6, 0, 7]
    if failure:
        with pytest.raises(OSError):
            check_ids._open_bus("COM9", ids, baud)
        assert bus.disconnects == [False]
    else:
        opened = check_ids._open_bus("COM9", ids, baud)
        assert opened is bus
        opened.disconnect(disable_torque=False)
        assert bus.disconnects == [False]
    assert bus.handshakes == [False]
    assert bus.bauds == ([baud] if baud != bus.default_baudrate and failure != "connect" else [])
