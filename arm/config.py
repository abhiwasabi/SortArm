"""Settings for the SO-101 arm. Override any value with an environment variable."""

from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from pathlib import Path

ARM_DIR = Path(__file__).resolve().parent

# LeRobot joint names for the SO-101 follower, in motor ID order 1-6.
JOINTS: tuple[str, ...] = (
    "shoulder_pan",
    "shoulder_lift",
    "elbow_flex",
    "wrist_flex",
    "wrist_roll",
    "gripper",
)
BODY_JOINTS: tuple[str, ...] = JOINTS[:-1]
GRIPPER = "gripper"

# Trial contract (issue #66): SORT <BIN> accepts exactly these, and each needs a taught pose
# named above_<bin> (above_recycle, above_trash). Not merged; the four-bin contract stays on main.
BINS: tuple[str, ...] = ("RECYCLE", "TRASH")


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    return float(raw) if raw not in (None, "") else default


@dataclass
class ArmConfig:
    # Serial port of the bus-servo board (Hiwonder BusLinker V3), e.g. "COM5" (Windows) or "/dev/ttyACM0".
    port: str | None = None
    # LeRobot robot id; the calibration file is <calibration_dir>/<robot_id>.json.
    robot_id: str = "sharkbyte_follower"
    calibration_dir: Path = field(default_factory=lambda: ARM_DIR / "calibration")
    poses_path: Path = field(default_factory=lambda: ARM_DIR / "poses.json")

    # Motion speed. Start slow; raise only after the poses are tested.
    speed_deg_s: float = 20.0  # body joints, degrees per second
    gripper_speed: float = 80.0  # gripper units (0-100) per second
    rate_hz: float = 50.0  # how often a new goal is sent during a move

    # LeRobot safety cap: a single goal may not differ from the present position
    # by more than this many degrees (gripper: units). Catches bad poses or bugs.
    max_relative_target: float = 10.0

    # A move is finished when every body joint is within this many degrees of its goal.
    # The gripper is not checked: when it closes on an item it stops early by design.
    tolerance_deg: float = 3.0
    settle_timeout_s: float = 3.0

    # Pauses that let the gripper finish closing or opening.
    grip_dwell_s: float = 0.6
    release_dwell_s: float = 0.6

    # Hard ceilings, deliberately not settable from the environment: a typo such as
    # ARM_SPEED_DEG_S=2000 is refused instead of making the arm fast. Change them here, on purpose.
    speed_limit_deg_s: float = 90.0
    gripper_speed_limit: float = 300.0
    relative_target_limit: float = 30.0

    def validate(self) -> None:
        """Reject settings that would break slow, interpolated motion (e.g. a typo like -20 or 2000)."""
        for name in ("speed_deg_s", "gripper_speed", "rate_hz", "max_relative_target",
                     "tolerance_deg", "settle_timeout_s"):
            value = getattr(self, name)
            if not (isinstance(value, (int, float)) and math.isfinite(value) and value > 0):
                raise ValueError(f"Arm setting {name} must be a positive number, got {value!r}.")
        for name in ("grip_dwell_s", "release_dwell_s"):
            value = getattr(self, name)
            if not (isinstance(value, (int, float)) and math.isfinite(value) and value >= 0):
                raise ValueError(f"Arm setting {name} must be zero or more, got {value!r}.")
        for name, limit in (("speed_deg_s", "speed_limit_deg_s"), ("gripper_speed", "gripper_speed_limit"),
                            ("max_relative_target", "relative_target_limit")):
            if getattr(self, name) > getattr(self, limit):
                raise ValueError(
                    f"Arm setting {name}={getattr(self, name)!r} is above the safety ceiling "
                    f"{limit}={getattr(self, limit)!r} (see arm/config.py)."
                )

    @classmethod
    def from_env(cls) -> "ArmConfig":
        cfg = cls()
        cfg.port = os.environ.get("ARM_PORT") or None
        cfg.robot_id = os.environ.get("ARM_ID") or cfg.robot_id
        if os.environ.get("ARM_CALIBRATION_DIR"):
            cfg.calibration_dir = Path(os.environ["ARM_CALIBRATION_DIR"])
        if os.environ.get("ARM_POSES"):
            cfg.poses_path = Path(os.environ["ARM_POSES"])
        cfg.speed_deg_s = _env_float("ARM_SPEED_DEG_S", cfg.speed_deg_s)
        cfg.gripper_speed = _env_float("ARM_GRIPPER_SPEED", cfg.gripper_speed)
        cfg.max_relative_target = _env_float("ARM_MAX_RELATIVE_TARGET", cfg.max_relative_target)
        return cfg
