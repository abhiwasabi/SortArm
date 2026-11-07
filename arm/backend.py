"""Hardware access for the arm.

`So101Backend` drives the real SO-101 through LeRobot. `MockBackend` simulates it so the
controller, the bridge, and tests can run without hardware. Both expose the same small API.
"""

from __future__ import annotations

import threading
from typing import Protocol

from .config import GRIPPER, JOINTS, ArmConfig

Positions = dict[str, float]


class Backend(Protocol):
    def read(self) -> Positions:
        """Present position of every joint (degrees; gripper 0-100)."""

    def write(self, goal: Positions) -> None:
        """Send goal positions. Joints left out keep their previous goal."""

    def set_torque(self, enabled: bool) -> None:
        """Enable or disable servo torque. Enabling first holds the present position."""

    def close(self) -> None:
        """Disconnect. On the real arm this also disables torque, so the arm goes limp."""


class CalibrationMissing(RuntimeError):
    pass


class So101Backend:
    """The real SO-101 follower arm on a USB bus-servo board, via LeRobot."""

    def __init__(self, config: ArmConfig):
        if not config.port:
            raise ValueError("Set ARM_PORT to the arm's serial port (find it with `lerobot-find-port`).")
        # Imported here so the mock and the tests do not need LeRobot installed.
        from lerobot.robots.so_follower import SO101Follower, SO101FollowerConfig

        # Cap every body-joint step at max_relative_target degrees. The gripper is left uncapped so it
        # can keep squeezing an item; LeRobot already limits its torque to 50%.
        caps = {joint: config.max_relative_target for joint in JOINTS if joint != GRIPPER}
        caps[GRIPPER] = 100.0
        robot_config = SO101FollowerConfig(
            port=config.port,
            id=config.robot_id,
            calibration_dir=config.calibration_dir,
            max_relative_target=caps,
            use_degrees=True,
        )
        self._robot = SO101Follower(robot_config)
        calibration_file = config.calibration_dir / f"{config.robot_id}.json"
        if not calibration_file.is_file():
            raise CalibrationMissing(
                f"No calibration at {calibration_file}. Run lerobot-calibrate first (see arm/README.md)."
            )
        self._robot.connect(calibrate=False)
        if not self._robot.is_calibrated:
            self._robot.disconnect()
            raise CalibrationMissing(
                f"The motors do not match {calibration_file}. Re-run lerobot-calibrate (see arm/README.md)."
            )
        # connect() leaves torque on. Hold where the arm is so it does not jump to a stale goal.
        self.write(self.read())

    def read(self) -> Positions:
        obs = self._robot.get_observation()
        return {joint: float(obs[f"{joint}.pos"]) for joint in JOINTS}

    def write(self, goal: Positions) -> None:
        self._robot.send_action({f"{joint}.pos": float(value) for joint, value in goal.items()})

    def set_torque(self, enabled: bool) -> None:
        if enabled:
            # Make the goal equal the present position first, so enabling does not snap the arm.
            present = self.read()
            self._robot.bus.sync_write("Goal_Position", present)
            self._robot.bus.enable_torque()
        else:
            self._robot.bus.disable_torque()

    def close(self) -> None:
        if self._robot.is_connected:
            self._robot.disconnect()


class MockBackend:
    """Simulated arm: each joint reaches its goal immediately unless it is listed in `stalled`.

    Set `fail = True` to simulate a lost connection: every read and write then raises ConnectionError.
    """

    def __init__(self, start: Positions | None = None, stalled: set[str] | None = None):
        self._lock = threading.Lock()
        self._position: Positions = {joint: 0.0 for joint in JOINTS}
        if start:
            self._position.update(start)
        self.stalled: set[str] = set(stalled or ())
        self.fail = False
        self.torque = True
        self.closed = False
        self.writes: list[Positions] = []

    def read(self) -> Positions:
        if self.fail:
            raise ConnectionError("mock arm not responding")
        with self._lock:
            return dict(self._position)

    def write(self, goal: Positions) -> None:
        if self.fail:
            raise ConnectionError("mock arm not responding")
        with self._lock:
            self.writes.append(dict(goal))
            if not self.torque:
                return
            for joint, value in goal.items():
                if joint not in self.stalled:
                    self._position[joint] = float(value)

    def set_torque(self, enabled: bool) -> None:
        self.torque = enabled

    def close(self) -> None:
        self.closed = True
