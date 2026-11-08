"""Named arm poses, stored in arm/poses.json and recorded with `python -m arm.teach`.

File layout:
    {
      "poses":   {"home": {"shoulder_pan": 0.0, ...5 body joints...}, ...},
      "gripper": {"open": 60.0, "closed": 5.0}
    }
Body joints are in degrees. The gripper is 0 (closed) to 100 (open) and is stored separately,
so a pose never changes the gripper by itself.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .config import BINS, BODY_JOINTS

GRIPPER_KEYS: tuple[str, ...] = ("open", "closed")
REQUIRED_POSES: tuple[str, ...] = ("home", "above_pick", "pick") + tuple(f"above_{b.lower()}" for b in BINS)


class PosesError(ValueError):
    pass


@dataclass
class Poses:
    poses: dict[str, dict[str, float]] = field(default_factory=dict)
    gripper: dict[str, float] = field(default_factory=dict)

    def missing(self) -> list[str]:
        """Names that still need to be taught, or are incomplete."""
        out = [name for name in REQUIRED_POSES if not _complete(self.poses.get(name))]
        out += [f"gripper {key}" for key in GRIPPER_KEYS if not isinstance(self.gripper.get(key), (int, float))]
        return out

    def validate(self) -> None:
        missing = self.missing()
        if missing:
            raise PosesError(
                "Poses not taught yet: " + ", ".join(missing) + ". Record them with `python -m arm.teach`."
            )

    def has(self, name: str) -> bool:
        """True if pose `name` is saved with all five body joints."""
        return _complete(self.poses.get(name))

    def has_grip(self, key: str) -> bool:
        return isinstance(self.gripper.get(key), (int, float))

    def body(self, name: str) -> dict[str, float]:
        if name not in self.poses:
            raise PosesError(f"Unknown pose {name!r}")
        return {joint: float(self.poses[name][joint]) for joint in BODY_JOINTS}

    def grip(self, key: str) -> float:
        return float(self.gripper[key])

    def to_json(self) -> dict:
        return {"poses": self.poses, "gripper": self.gripper}


def _complete(pose: dict | None) -> bool:
    return isinstance(pose, dict) and all(isinstance(pose.get(j), (int, float)) for j in BODY_JOINTS)


def load(path: Path) -> Poses:
    if not path.is_file():
        return Poses()
    data = json.loads(path.read_text(encoding="utf-8"))
    return Poses(poses=dict(data.get("poses") or {}), gripper=dict(data.get("gripper") or {}))


def save(poses: Poses, path: Path) -> None:
    path.write_text(json.dumps(poses.to_json(), indent=2) + "\n", encoding="utf-8")


def demo_poses() -> Poses:
    """Made-up poses for the mock arm only. Never use these on real hardware."""
    def pose(pan: float, lift: float, elbow: float, flex: float) -> dict[str, float]:
        return {"shoulder_pan": pan, "shoulder_lift": lift, "elbow_flex": elbow, "wrist_flex": flex, "wrist_roll": 0.0}

    return Poses(
        poses={
            "home": pose(0, -90, 90, 0),
            "above_pick": pose(0, -30, 40, 50),
            "pick": pose(0, -10, 35, 60),
            # Two bins on opposite sides of the base. Mock only; teach the real poses.
            "above_plastic": pose(-50, -40, 50, 40),
            "above_metal": pose(50, -40, 50, 40),
        },
        gripper={"open": 60.0, "closed": 5.0},
    )
