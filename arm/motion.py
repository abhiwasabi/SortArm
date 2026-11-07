"""Slow, stoppable joint-space moves."""

from __future__ import annotations

import math
import threading
import time

from .backend import Backend, Positions
from .config import BODY_JOINTS, GRIPPER, JOINTS, ArmConfig


class MotionError(RuntimeError):
    """A move did not finish: a joint did not reach its goal in time. The arm is holding."""

    def __init__(self, message: str, held: Positions | None = None):
        super().__init__(message)
        self.held = held


class MotionStopped(Exception):
    """A move was cancelled by STOP. The arm is holding (`held` is the goal it holds, if it moved)."""

    def __init__(self, held: Positions | None = None):
        super().__init__("stopped")
        self.held = held


def hold(backend: Backend, gripper: float | None = None) -> Positions:
    """Freeze the arm where it is and return the goal now commanded.

    Body joints get their present position as the goal. The gripper keeps `gripper`, its last
    commanded value, so a carried item stays gripped and a closing gripper closes no further.
    Without `gripper`, the gripper also holds its present position.
    """
    goal = backend.read()
    if gripper is not None:
        goal[GRIPPER] = float(gripper)
    backend.write(goal)
    return goal


def move_to(backend: Backend, start: Positions | None, target: Positions, config: ArmConfig,
            stop: threading.Event) -> Positions:
    """Move to `target` in a straight line in joint space at the configured speed.

    `start` is where interpolation begins (None: the present position). The controller passes the
    last commanded goal where it still matches the arm (see ArmController._start_point): for the
    gripper this matters, since a gripper stalled on an item sits short of "closed", and commanding
    its present position would let go.

    Joints missing from `target` keep their start value. Every step checks `stop`; when it is set
    the arm holds and MotionStopped is raised. Afterwards it waits until every body joint is within
    tolerance, re-sending the goal (the gripper is not checked: it may be closed on an item).
    Returns the goal now commanded.
    """
    if start is None:
        start = backend.read()
    start = {joint: float(start[joint]) for joint in JOINTS}
    goal = {joint: float(target.get(joint, start[joint])) for joint in JOINTS}

    duration = 0.0
    for joint in JOINTS:
        speed = config.gripper_speed if joint == GRIPPER else config.speed_deg_s
        duration = max(duration, abs(goal[joint] - start[joint]) / speed)
    period = 1.0 / config.rate_hz
    steps = max(1, math.ceil(duration / period))

    commanded = start
    t0 = time.monotonic()
    for i in range(1, steps + 1):
        if stop.is_set():
            raise MotionStopped(hold(backend, commanded[GRIPPER]))
        if i == steps:
            commanded = dict(goal)
        else:
            frac = i / steps
            commanded = {joint: start[joint] + (goal[joint] - start[joint]) * frac for joint in JOINTS}
        backend.write(commanded)
        stop.wait(max(0.0, t0 + i * period - time.monotonic()))

    _settle(backend, goal, config, stop)
    return goal


def _settle(backend: Backend, goal: Positions, config: ArmConfig, stop: threading.Event) -> None:
    deadline = time.monotonic() + config.settle_timeout_s
    period = 1.0 / config.rate_hz
    while True:
        if stop.is_set():
            raise MotionStopped(hold(backend, goal[GRIPPER]))
        present = backend.read()
        errors = {joint: abs(present[joint] - goal[joint]) for joint in BODY_JOINTS}
        worst = max(errors, key=errors.get)
        if errors[worst] <= config.tolerance_deg:
            return
        if time.monotonic() >= deadline:
            held = hold(backend, goal[GRIPPER])
            raise MotionError(
                f"{worst} did not reach its goal: at {present[worst]:.1f}, goal {goal[worst]:.1f} "
                f"(tolerance {config.tolerance_deg})",
                held,
            )
        # A write is clipped by LeRobot when the goal is more than max_relative_target from the present
        # position (the arm lagged behind). Re-send it once the arm has caught up to within that cap,
        # so the full goal arrives. Never re-send a goal that is still farther away: that would turn
        # the safety cap into a fast move toward a bad target. Such a move times out and holds instead.
        if errors[worst] <= config.max_relative_target:
            backend.write(goal)
        stop.wait(period)


def pause(seconds: float, stop: threading.Event) -> None:
    """Wait, but end early (raising MotionStopped) if STOP arrives. The arm keeps its goal."""
    if stop.wait(seconds):
        raise MotionStopped()
