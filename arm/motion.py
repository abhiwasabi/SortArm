"""Slow, stoppable joint-space moves."""

from __future__ import annotations

import math
import threading
import time

from .backend import Backend, Positions
from .config import BODY_JOINTS, GRIPPER, JOINTS, ArmConfig


class MotionError(RuntimeError):
    """A move did not finish (timeout, or the arm could not be read or written)."""


class MotionStopped(Exception):
    """A move was cancelled by STOP. The arm is holding its position."""


def hold(backend: Backend) -> None:
    """Make the goal equal the present position, so the arm stays where it is with torque on."""
    backend.write(backend.read())


def move_to(backend: Backend, target: Positions, config: ArmConfig, stop: threading.Event) -> None:
    """Move to `target` in a straight line in joint space at the configured speed.

    Joints missing from `target` stay where they are. Every step checks `stop`; when it is set the
    arm holds its position and MotionStopped is raised. After the last step, waits until every body
    joint is within tolerance (the gripper is not checked: it may be closed on an item).
    """
    start = backend.read()
    goal = {joint: float(target.get(joint, start[joint])) for joint in JOINTS}

    duration = 0.0
    for joint in JOINTS:
        speed = config.gripper_speed if joint == GRIPPER else config.speed_deg_s
        duration = max(duration, abs(goal[joint] - start[joint]) / speed)
    period = 1.0 / config.rate_hz
    steps = max(1, math.ceil(duration / period))

    t0 = time.monotonic()
    for i in range(1, steps + 1):
        if stop.is_set():
            hold(backend)
            raise MotionStopped()
        if i == steps:
            backend.write(dict(goal))
        else:
            frac = i / steps
            backend.write({joint: start[joint] + (goal[joint] - start[joint]) * frac for joint in JOINTS})
        stop.wait(max(0.0, t0 + i * period - time.monotonic()))

    _settle(backend, goal, config, stop)


def _settle(backend: Backend, goal: Positions, config: ArmConfig, stop: threading.Event) -> None:
    deadline = time.monotonic() + config.settle_timeout_s
    period = 1.0 / config.rate_hz
    while True:
        if stop.is_set():
            hold(backend)
            raise MotionStopped()
        present = backend.read()
        errors = {joint: abs(present[joint] - goal[joint]) for joint in BODY_JOINTS}
        worst = max(errors, key=errors.get)
        if errors[worst] <= config.tolerance_deg:
            return
        if time.monotonic() >= deadline:
            hold(backend)
            raise MotionError(
                f"{worst} did not reach its goal: at {present[worst]:.1f}, goal {goal[worst]:.1f} "
                f"(tolerance {config.tolerance_deg})"
            )
        stop.wait(period)


def pause(seconds: float, stop: threading.Event) -> None:
    """Wait, but end early (raising MotionStopped) if STOP arrives."""
    if stop.wait(seconds):
        raise MotionStopped()
