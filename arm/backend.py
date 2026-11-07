"""Hardware access for the arm.

`So101Backend` drives the real SO-101 through LeRobot. `MockBackend` simulates it so the
controller, the bridge, and tests can run without hardware. Both expose the same small API.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from typing import Any, Protocol

from .config import BODY_JOINTS, GRIPPER, JOINTS, ArmConfig

logger = logging.getLogger(__name__)

Positions = dict[str, float]


class Backend(Protocol):
    def read(self) -> Positions:
        """Present position of every joint (degrees; gripper 0-100)."""

    def write(self, goal: Positions) -> None:
        """Send goal positions. The goal must name all six joints (ValueError otherwise)."""

    def set_torque(self, enabled: bool) -> None:
        """Enable or disable servo torque. Enabling first holds the present position."""

    def close(self) -> None:
        """Disconnect. On the real arm this also disables torque, so the arm goes limp."""


class CalibrationMissing(RuntimeError):
    pass


class ArmConnectionError(ConnectionError):
    """The arm stopped answering: USB unplugged, servo power lost, or corrupted packets."""


def check_full_goal(goal: Positions) -> None:
    if set(goal) != set(JOINTS):
        raise ValueError(f"A goal must name all six joints {JOINTS}; got {sorted(goal)}.")


# LeRobot 0.7 reports port and bus transport failures as ConnectionError (an OSError). Its
# RuntimeErrors mean something else (a servo refused a register, a model or firmware mismatch), so
# they pass through unchanged and are reported as a failed command, not a lost connection.
_TRANSPORT_ERRORS = (OSError,)


class So101Backend:
    """The real SO-101 follower arm on a USB bus-servo board, via LeRobot.

    `robot` is for tests only: an object with LeRobot's SO101Follower interface.
    """

    def __init__(self, config: ArmConfig, robot: Any = None):
        if robot is None:
            robot = self._make_robot(config)
        self._robot = robot
        self._connect()

    @staticmethod
    def _make_robot(config: ArmConfig) -> Any:
        if not config.port:
            raise ValueError("Set ARM_PORT to the arm's serial port (find it with `lerobot-find-port`).")
        calibration_file = config.calibration_dir / f"{config.robot_id}.json"
        if not calibration_file.is_file():
            raise CalibrationMissing(
                f"No calibration at {calibration_file}. Run lerobot-calibrate first (see arm/README.md)."
            )
        # Imported here so the mock and the tests do not need LeRobot installed.
        from lerobot.robots.so_follower import SO101Follower, SO101FollowerConfig

        # Cap every body-joint step at max_relative_target degrees. The gripper is left uncapped so it
        # can keep squeezing an item; LeRobot already limits its torque to 50%.
        caps = {joint: config.max_relative_target for joint in BODY_JOINTS}
        caps[GRIPPER] = 100.0
        return SO101Follower(
            SO101FollowerConfig(
                port=config.port,
                id=config.robot_id,
                calibration_dir=config.calibration_dir,
                max_relative_target=caps,
                use_degrees=True,
            )
        )

    def _connect(self) -> None:
        """LeRobot's connect(), reordered so torque never comes on with a stale goal.

        SO101Follower.connect() is bus.connect() then configure(), and configure() turns torque
        back on at the end. Between the two we set every goal to the present position.
        """
        bus = self._robot.bus
        try:
            bus.connect()  # opens the port and checks that every servo answers with the right model
        except _TRANSPORT_ERRORS as exc:
            raise ArmConnectionError(f"Could not connect to the arm: {exc}") from exc
        try:
            if not self._robot.is_calibrated:
                raise CalibrationMissing(
                    "The motors do not match the calibration file. Re-run lerobot-calibrate (see arm/README.md)."
                )
            bus.disable_torque()
            bus.sync_write("Goal_Position", bus.sync_read("Present_Position"))
            self._robot.configure()  # LeRobot's servo settings; re-enables torque, holding position
        except _TRANSPORT_ERRORS as exc:
            self._close_quietly()
            raise ArmConnectionError(f"Arm setup failed: {exc}") from exc
        except BaseException:  # calibration mismatch, a servo error, Ctrl+C: never leave the port open
            self._close_quietly()
            raise

    def read(self) -> Positions:
        try:
            obs = self._robot.get_observation()
        except _TRANSPORT_ERRORS as exc:
            raise ArmConnectionError(f"read failed: {exc}") from exc
        return {joint: float(obs[f"{joint}.pos"]) for joint in JOINTS}

    def write(self, goal: Positions) -> None:
        check_full_goal(goal)
        try:
            self._robot.send_action({f"{joint}.pos": float(value) for joint, value in goal.items()})
        except _TRANSPORT_ERRORS as exc:
            raise ArmConnectionError(f"write failed: {exc}") from exc

    def set_torque(self, enabled: bool) -> None:
        bus = self._robot.bus
        try:
            if enabled:
                # Make the goal equal the present position first, so enabling does not snap the arm.
                bus.sync_write("Goal_Position", bus.sync_read("Present_Position"))
                bus.enable_torque()
            else:
                bus.disable_torque()
        except _TRANSPORT_ERRORS as exc:
            raise ArmConnectionError(f"torque change failed: {exc}") from exc

    def close(self) -> None:
        self._close_quietly()

    def _close_quietly(self) -> None:
        try:
            if self._robot.is_connected:
                self._robot.disconnect()
        except Exception:
            logger.exception("could not disconnect cleanly")


class MockBackend:
    """Simulated arm: each joint reaches its goal immediately unless it is listed in `stalled`.

    - `max_step` emulates LeRobot's per-step cap: a body joint moves at most this far per write.
    - Set `fail` to an exception class (e.g. ConnectionError) to make every read and write raise it;
      `fail_once` makes only the next read raise.
    - `read_gate` / `write_gate`: when set to an Event, reads / writes wait for it (a hung bus).
    - `write_hook(goal)` runs before each write is applied: it may sleep (a delayed bus write) or
      raise (a servo error). `write_times` records when each write was applied.
    - After `close()`, a write raises ConnectionError (as LeRobot does) and counts in
      `writes_after_close`.
    - `reads` counts reads, `read_threads` records which thread made each one, and `writes` keeps
      every goal sent. `move_by_hand()` changes a joint as if pushed (or sagging with power off).
    """

    def __init__(self, start: Positions | None = None, stalled: set[str] | None = None,
                 max_step: float | None = None):
        self._lock = threading.Lock()
        self._position: Positions = {joint: 0.0 for joint in JOINTS}
        if start:
            self._position.update(start)
        self.stalled: set[str] = set(stalled or ())
        self.max_step = max_step
        self.fail: type[BaseException] | None = None
        self.fail_once: type[BaseException] | None = None
        self.read_gate: threading.Event | None = None
        self.write_gate: threading.Event | None = None
        self.write_hook: Callable[[Positions], None] | None = None
        self.torque = True
        self.closed = False
        self.writes_after_close = 0
        self.writes: list[Positions] = []
        self.write_times: list[float] = []
        self.reads = 0
        self.read_threads: list[str] = []

    def move_by_hand(self, joint: str, value: float) -> None:
        with self._lock:
            self._position[joint] = float(value)

    def read(self) -> Positions:
        gate = self.read_gate
        if gate is not None:
            gate.wait()
        if self.fail_once:
            error, self.fail_once = self.fail_once, None
            raise error("mock arm glitch")
        if self.fail:
            raise self.fail("mock arm not responding")
        with self._lock:
            self.reads += 1
            self.read_threads.append(threading.current_thread().name)
            return dict(self._position)

    def write(self, goal: Positions) -> None:
        check_full_goal(goal)
        gate = self.write_gate
        if gate is not None:
            gate.wait()
        if self.closed:
            self.writes_after_close += 1
            raise ConnectionError("mock arm is closed")
        hook = self.write_hook
        if hook is not None:
            hook(goal)
        if self.fail:
            raise self.fail("mock arm not responding")
        with self._lock:
            self.writes.append(dict(goal))
            self.write_times.append(time.perf_counter())
            if not self.torque:
                return
            for joint, value in goal.items():
                if joint in self.stalled:
                    continue
                target = float(value)
                if self.max_step is not None and joint != GRIPPER:
                    present = self._position[joint]
                    target = max(present - self.max_step, min(present + self.max_step, target))
                self._position[joint] = target

    def set_torque(self, enabled: bool) -> None:
        self.torque = enabled

    def close(self) -> None:
        self.closed = True
