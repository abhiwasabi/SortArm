"""So101Backend against a fake LeRobot robot: connect order, cleanup, and error wrapping."""

from __future__ import annotations

import pytest

from arm.backend import ArmConnectionError, CalibrationMissing, So101Backend
from arm.config import JOINTS, ArmConfig

PRESENT = {joint: float(i) for i, joint in enumerate(JOINTS)}


class FakeBus:
    def __init__(self, log: list, fail: dict[str, BaseException] | None = None):
        self.log = log
        self.fail = fail or {}
        self.is_connected = False

    def _maybe_fail(self, name: str) -> None:
        if name in self.fail:
            raise self.fail[name]

    def connect(self):
        self.log.append("connect")
        self._maybe_fail("connect")
        self.is_connected = True

    def disable_torque(self):
        self.log.append("disable_torque")

    def enable_torque(self):
        self.log.append("enable_torque")

    def sync_read(self, name):
        self.log.append(("sync_read", name))
        self._maybe_fail("sync_read")
        return dict(PRESENT)

    def sync_write(self, name, values):
        self.log.append(("sync_write", name, dict(values)))


class FakeRobot:
    def __init__(self, calibrated: bool = True, fail: dict[str, BaseException] | None = None):
        self.log: list = []
        self.bus = FakeBus(self.log, fail)
        self.calibrated = calibrated
        self.fail = fail or {}

    @property
    def is_calibrated(self):
        return self.calibrated

    @property
    def is_connected(self):
        return self.bus.is_connected

    def configure(self):
        self.log.append("configure")

    def get_observation(self):
        if "get_observation" in self.fail:
            raise self.fail["get_observation"]
        return {f"{joint}.pos": value for joint, value in PRESENT.items()}

    def send_action(self, action):
        self.log.append(("send_action", dict(action)))
        return action

    def disconnect(self):
        self.log.append("disconnect")
        self.bus.is_connected = False


def test_connect_sets_goal_to_present_before_torque_comes_back_on():
    robot = FakeRobot()
    So101Backend(ArmConfig(), robot=robot)
    assert robot.log == [
        "connect",
        "disable_torque",
        ("sync_read", "Present_Position"),
        ("sync_write", "Goal_Position", PRESENT),
        "configure",  # LeRobot's configure() turns torque back on
    ]


def test_uncalibrated_arm_is_refused_and_the_port_closed():
    robot = FakeRobot(calibrated=False)
    with pytest.raises(CalibrationMissing):
        So101Backend(ArmConfig(), robot=robot)
    assert robot.log[-1] == "disconnect"
    assert "configure" not in robot.log


@pytest.mark.parametrize("error, expected", [
    (ConnectionError("port closed"), ArmConnectionError),  # transport failure
    (RuntimeError("Failed to sync read: motor 2 refused"), RuntimeError),  # servo error, not transport
    (KeyboardInterrupt(), KeyboardInterrupt),
])
def test_failure_during_setup_closes_the_port(error, expected):
    robot = FakeRobot(fail={"sync_read": error})
    with pytest.raises(expected):
        So101Backend(ArmConfig(), robot=robot)
    assert robot.log[-1] == "disconnect"


def test_wrong_port_is_a_connection_error():
    robot = FakeRobot(fail={"connect": ConnectionError("Could not connect on port 'COM99'")})
    with pytest.raises(ArmConnectionError):
        So101Backend(ArmConfig(), robot=robot)
    assert "disconnect" not in robot.log


def test_transport_read_failure_is_a_connection_error():
    robot = FakeRobot()
    backend = So101Backend(ArmConfig(), robot=robot)
    robot.fail["get_observation"] = ConnectionError("Failed to sync read: no status packet")
    with pytest.raises(ArmConnectionError):
        backend.read()


def test_servo_error_is_not_a_connection_error():
    robot = FakeRobot()
    backend = So101Backend(ArmConfig(), robot=robot)
    robot.fail["get_observation"] = RuntimeError("Failed to sync read: motor 3 refused")
    with pytest.raises(RuntimeError) as info:
        backend.read()
    assert not isinstance(info.value, ConnectionError)


def test_partial_goal_is_refused_before_reaching_lerobot():
    robot = FakeRobot()
    backend = So101Backend(ArmConfig(), robot=robot)
    with pytest.raises(ValueError):
        backend.write({"gripper": 10.0})
    assert not any(entry[0] == "send_action" for entry in robot.log if isinstance(entry, tuple))


def test_enabling_torque_holds_present_position_first():
    robot = FakeRobot()
    backend = So101Backend(ArmConfig(), robot=robot)
    robot.log.clear()
    backend.set_torque(True)
    assert robot.log == [
        ("sync_read", "Present_Position"),
        ("sync_write", "Goal_Position", PRESENT),
        "enable_torque",
    ]


def test_missing_port_or_calibration_is_reported_before_lerobot_is_touched(tmp_path):
    with pytest.raises(ValueError):
        So101Backend(ArmConfig(port=None))
    with pytest.raises(CalibrationMissing):
        So101Backend(ArmConfig(port="COM99", calibration_dir=tmp_path))
