"""Estimate pickup and bin reach from the official SO-101 joint chain."""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path
from typing import Mapping, Sequence

import numpy as np

from .config import BINS

NOTICE = (
    "Planning estimate from the official SO-101 model "
    "(TheRobotStudio/SO-ARM100, Apache-2.0). Real positions come from "
    "python -m arm.teach on the assembled arm."
)
DOWN = np.array([0.0, 0.0, -1.0])
POSITION_TOLERANCE_M = 0.005
AXIS_WEIGHT = 0.08
# Allowed angle between the gripper and straight down. With its wrist limits the SO-101 can
# only point straight down up to ~11 cm above the table, so drops (bin rim + clearance, ~20 cm)
# need a tilted gripper; releasing over a bin opening does not need a vertical gripper.
DEFAULT_PICK_TILT_DEG = 15.0
DEFAULT_DROP_TILT_DEG = 60.0
TILT_TOLERANCE_DEG = 0.5
SEED_GRID_POINTS = 25  # per joint (shoulder_lift, elbow_flex, wrist_flex)
SEEDS_PER_TARGET = 6


def _cone_target(axis: np.ndarray, max_tilt_rad: np.ndarray) -> np.ndarray:
    """The closest gripper direction allowed by each tilt cone (the axis itself if inside)."""
    tilt = np.arccos(np.clip(-axis[:, 2], -1.0, 1.0))
    sideways = axis.copy()
    sideways[:, 2] = 0.0
    norm = np.linalg.norm(sideways, axis=1)
    unit = np.where(norm[:, None] > 1e-9, sideways / np.maximum(norm, 1e-12)[:, None], [1.0, 0.0, 0.0])
    boundary = np.cos(max_tilt_rad)[:, None] * DOWN + np.sin(max_tilt_rad)[:, None] * unit
    return np.where((tilt <= max_tilt_rad)[:, None], axis, boundary)


def _rotation(axis: np.ndarray, angle: np.ndarray) -> np.ndarray:
    """Rodrigues rotation, also accepting a batch of angles."""
    x, y, z = axis / np.linalg.norm(axis)
    skew = np.array([[0.0, -z, y], [z, 0.0, -x], [-y, x, 0.0]])
    outer = np.outer([x, y, z], [x, y, z])
    c, s = np.cos(angle)[..., None, None], np.sin(angle)[..., None, None]
    return c * np.eye(3) + (1.0 - c) * outer + s * skew


def _rpy(angles: Sequence[float]) -> np.ndarray:
    roll, pitch, yaw = angles
    return (
        _rotation(np.array([0.0, 0.0, 1.0]), np.asarray(yaw))
        @ _rotation(np.array([0.0, 1.0, 0.0]), np.asarray(pitch))
        @ _rotation(np.array([1.0, 0.0, 0.0]), np.asarray(roll))
    )


@dataclass(frozen=True)
class ReachResult:
    """A static reach estimate; angles are URDF degrees, never servo goals."""

    reachable: bool
    urdf_angles_deg: dict[str, float]
    margin_deg: float
    position_error_cm: float
    tilt_deg: float  # angle between the gripper and straight down
    max_tilt_deg: float


class Kinematics:
    """NumPy FK and bounded damped least-squares IK. No hardware access."""

    def __init__(self, geometry_path: Path | None = None):
        path = geometry_path or Path(__file__).with_name("so101_geometry.json")
        self.joints = json.loads(path.read_text(encoding="utf-8"))["joints"]
        moving = [joint for joint in self.joints if joint["type"] == "revolute"]
        self.names = tuple(joint["name"] for joint in moving)
        self.limits = np.asarray([joint["limit_rad"] for joint in moving], dtype=float)
        self._origins = [
            (np.asarray(joint["xyz_m"]), _rpy(joint["rpy_rad"])) for joint in self.joints
        ]
        # The sum of link lengths is a conservative rejection bound about base_link.
        self._max_reach_m = sum(float(np.linalg.norm(xyz)) for xyz, _ in self._origins)
        self._grid: tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray] | None = None

    def _seed_grid(self) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        """Cached FK samples of the arm's vertical plane: (q, radius from pan axis, z, tilt)."""
        if self._grid is None:
            axes = [np.linspace(low, high, SEED_GRID_POINTS) for low, high in self.limits[1:4]]
            lift, elbow, flex = (a.ravel() for a in np.meshgrid(*axes, indexing="ij"))
            q = np.zeros((lift.size, len(self.names)))
            q[:, 1], q[:, 2], q[:, 3] = lift, elbow, flex
            position, axis = self._fk(q)
            pan_x, pan_y = self._origins[0][0][:2]
            radius = np.hypot(position[:, 0] - pan_x, position[:, 1] - pan_y)
            tilt = np.arccos(np.clip(-axis[:, 2], -1.0, 1.0))  # unchanged by pan
            self._grid = (q, radius, position[:, 2], tilt)
        return self._grid

    def _fk(self, angles: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Compose Translate(xyz), Rz Ry Rx, then joint rotation in order."""
        shape = angles.shape[:-1]
        rotation = np.broadcast_to(np.eye(3), (*shape, 3, 3)).copy()
        position = np.zeros((*shape, 3))
        index = 0
        for joint, (xyz, origin_rotation) in zip(self.joints, self._origins):
            position += (rotation @ xyz[..., None])[..., 0]
            rotation = rotation @ origin_rotation
            if joint["type"] == "revolute":
                rotation = rotation @ _rotation(np.asarray(joint["axis"]), angles[..., index])
                index += 1
        return position, rotation[..., :, 2]

    def forward(self, angles_rad: Sequence[float] | Mapping[str, float]) -> tuple[np.ndarray, np.ndarray]:
        """Return tool position in metres and tool z axis for URDF radians."""
        if isinstance(angles_rad, Mapping):
            angles_rad = [angles_rad.get(name, 0.0) for name in self.names]
        angles = np.asarray(angles_rad, dtype=float)
        if angles.shape != (len(self.names),) or not np.isfinite(angles).all():
            raise ValueError(f"Supply {len(self.names)} finite URDF joint angles in radians.")
        return self._fk(angles)

    def inverse(self, point_cm: Sequence[float], max_tilt_deg: float = 5.0) -> ReachResult:
        """Solve a point in cm with the gripper within `max_tilt_deg` of straight down."""
        return self.inverse_many([point_cm], [max_tilt_deg])[0]

    def _seeds(self, target: np.ndarray, max_tilt_rad: float) -> np.ndarray:
        """Grid poses closest to the target in the arm's vertical plane, roughly inside the cone."""
        q, radius, z, tilt = self._seed_grid()
        pan_x, pan_y = self._origins[0][0][:2]
        target_radius = math.hypot(target[0] - pan_x, target[1] - pan_y)
        distance = (radius - target_radius) ** 2 + (z - target[2]) ** 2
        distance = distance + np.where(tilt <= max_tilt_rad + math.radians(10), 0.0, 1.0)
        best = np.argsort(distance)[:SEEDS_PER_TARGET]
        seeds = q[best].copy()
        # Joint 1's fixed rotation turns its local +z axis downward, hence the minus sign.
        seeds[:, 0] = -math.atan2(target[1] - pan_y, target[0] - pan_x)
        return seeds

    def inverse_many(self, points_cm: Sequence[Sequence[float]],
                     max_tilts_deg: Sequence[float] | None = None) -> list[ReachResult]:
        """Solve independent targets in a batch, each with its own gripper tilt allowance."""
        targets = np.asarray(points_cm, dtype=float)
        if targets.size == 0:
            return []
        if targets.ndim != 2 or targets.shape[1] != 3 or not np.isfinite(targets).all():
            raise ValueError("Targets must have three finite coordinates in cm.")
        tilts_deg = np.full(len(targets), 5.0) if max_tilts_deg is None else np.asarray(max_tilts_deg, float)
        if tilts_deg.shape != (len(targets),) or not np.isfinite(tilts_deg).all() or np.any(tilts_deg < 0):
            raise ValueError("Give one finite, non-negative tilt allowance per target.")
        targets = targets / 100.0
        count, nseed = len(targets), SEEDS_PER_TARGET
        q = np.concatenate([self._seeds(t, math.radians(m)) for t, m in zip(targets, tilts_deg)])
        q = np.clip(q, self.limits[:, 0], self.limits[:, 1])
        goal = np.repeat(targets, nseed, axis=0)
        cone = np.repeat(np.radians(tilts_deg), nseed)
        active = np.repeat(np.linalg.norm(targets, axis=1) <= self._max_reach_m, nseed)
        previous_cost = np.full(len(q), np.inf)
        stalled = np.zeros(len(q), dtype=int)
        epsilon = 1e-5

        def cost_of(position: np.ndarray, axis: np.ndarray, ids: np.ndarray) -> np.ndarray:
            off_cone = _cone_target(axis, cone[ids]) - axis  # zero while inside the cone
            return np.sum((goal[ids] - position) ** 2, axis=1) + AXIS_WEIGHT ** 2 * np.sum(off_cone ** 2, axis=1)

        for _ in range(80):
            ids = np.flatnonzero(active)
            if not len(ids):
                break
            current = q[ids]
            position, axis = self._fk(current)
            # Pull the gripper only as far as the nearest allowed direction (fixed for this step).
            allowed = _cone_target(axis, cone[ids])
            value = np.concatenate((position, AXIS_WEIGHT * axis), axis=1)
            error = np.concatenate((goal[ids] - position, AXIS_WEIGHT * (allowed - axis)), axis=1)
            cost = np.sum(error * error, axis=1)
            converged = (np.linalg.norm(error[:, :3], axis=1) < 1e-5) & (np.linalg.norm(error[:, 3:], axis=1) < 1e-6)
            stalled[ids] = np.where(np.abs(previous_cost[ids] - cost) < 1e-11, stalled[ids] + 1, 0)
            previous_cost[ids] = cost
            active[ids[converged | (stalled[ids] >= 4)]] = False
            jacobian = np.empty((len(ids), 6, 4))
            for joint in range(4):
                shifted = current.copy()
                shifted[:, joint] += epsilon
                pos_shift, axis_shift = self._fk(shifted)
                shifted_value = np.concatenate((pos_shift, AXIS_WEIGHT * axis_shift), axis=1)
                jacobian[:, :, joint] = (shifted_value - value) / epsilon
            transpose = jacobian.transpose(0, 2, 1)
            normal = transpose @ jacobian + 0.003 ** 2 * np.eye(4)
            step = np.linalg.solve(normal, (transpose @ error[..., None]))[..., 0]
            # Bound the step, then backtrack so saturated seeds cannot oscillate.
            scale = np.maximum(1.0, np.max(np.abs(step), axis=1) / 0.3)
            step /= scale[:, None]
            accepted = np.zeros(len(ids), dtype=bool)
            for factor in (1.0, 0.5, 0.25):
                trial = current.copy()
                trial[:, :4] += factor * step
                trial = np.clip(trial, self.limits[:, 0], self.limits[:, 1])
                trial_cost = cost_of(*self._fk(trial), ids)
                improve = (~accepted) & (trial_cost < cost) & (~converged)
                q[ids[improve]] = trial[improve]
                accepted |= improve
                if accepted.all():
                    break
        position, axis = self._fk(q)
        position_error = np.linalg.norm(position - goal, axis=1)
        tilt = np.degrees(np.arccos(np.clip(axis @ DOWN, -1.0, 1.0)))
        margin = np.degrees(np.min(np.minimum(q - self.limits[:, 0], self.limits[:, 1] - q), axis=1))
        success = (position_error <= POSITION_TOLERANCE_M) & (tilt <= np.degrees(cone) + TILT_TOLERANCE_DEG)
        results = []
        for index in range(count):
            indices = np.arange(index * nseed, (index + 1) * nseed)
            valid = indices[success[indices]]
            excess = np.radians(np.maximum(0.0, tilt[indices] - tilts_deg[index]))
            best = int(valid[np.argmax(margin[valid])]) if len(valid) else int(
                indices[np.argmin(position_error[indices] ** 2 + (AXIS_WEIGHT * excess) ** 2)]
            )
            results.append(ReachResult(
                bool(success[best]), dict(zip(self.names, map(float, np.degrees(q[best])))),
                float(margin[best]), float(position_error[best] * 100), float(tilt[best]),
                float(tilts_deg[index]),
            ))
        return results


@lru_cache(maxsize=1)
def model() -> Kinematics:
    return Kinematics()


def _finite_nonnegative(**values: float) -> None:
    for name, value in values.items():
        if not math.isfinite(value) or value < 0:
            raise ValueError(f"{name.replace('_', ' ')} must be finite and zero or more.")


def _check_tilts(pick_tilt: float, drop_tilt: float) -> None:
    for name, value in (("pick tilt", pick_tilt), ("drop tilt", drop_tilt)):
        if not math.isfinite(value) or not 0 <= value <= 90:
            raise ValueError(f"{name} must be between 0 and 90 degrees.")


def check_layout(pickup: Sequence[float], bins: Mapping[str, Sequence[float]],
                 item_height: float = 6.0, bin_height: float = 15.0,
                 clearance: float = 5.0, pick_tilt: float = DEFAULT_PICK_TILT_DEG,
                 drop_tilt: float = DEFAULT_DROP_TILT_DEG) -> list[dict]:
    """Check the grasp, approach, and all four drop points (coordinates in cm).

    Pickup points allow the gripper `pick_tilt` degrees from straight down; drops allow `drop_tilt`.
    """
    _finite_nonnegative(item_height=item_height, bin_height=bin_height, clearance=clearance)
    _check_tilts(pick_tilt, drop_tilt)
    if set(bins) != set(BINS):
        raise ValueError(f"Supply each bin exactly once: {', '.join(BINS)}.")
    coordinates = [pickup, *(bins[name] for name in BINS)]
    if any(len(point) != 2 or not all(math.isfinite(v) for v in point) for point in coordinates):
        raise ValueError("Pickup and bin positions must be two finite coordinates in cm.")
    names = ["pickup_grasp", "pickup_approach", *BINS]
    points = [(*pickup, item_height / 2), (*pickup, item_height + 5)]
    points.extend((*bins[name], bin_height + clearance) for name in BINS)
    tilts = [pick_tilt, pick_tilt, *([drop_tilt] * len(BINS))]
    return [dict(name=name, x_cm=float(point[0]), y_cm=float(point[1]), z_cm=float(point[2]),
                 **asdict(result))
            for name, point, result in zip(names, points, model().inverse_many(points, tilts))]


def _footprints_fit(points: np.ndarray, pickup: Sequence[float], width: float,
                    gap: float, keep_clear: str) -> bool:
    if keep_clear == "left" and np.any(points[:, 1] + width / 2 > 1e-8):
        return False
    if keep_clear == "right" and np.any(points[:, 1] - width / 2 < -1e-8):
        return False
    # Axis-aligned squares expanded by the requested gap must not overlap.
    for first in range(len(points)):
        for second in range(first):
            if np.max(np.abs(points[first] - points[second])) < width + gap - 1e-8:
                return False
    # Bin centres stay outside the specified circular pickup keep-out.
    if np.any(np.linalg.norm(points - pickup, axis=1) < width / 2 + gap - 1e-8):
        return False
    # Keep the base origin outside the physical footprint as well.
    return not bool(np.any(np.max(np.abs(points), axis=1) < width / 2))


@lru_cache(maxsize=16)
def suggest_layout(pickup: tuple[float, float] = (20.0, 0.0), item_height: float = 6.0,
                   bin_width: float = 15.0, bin_height: float = 15.0,
                   clearance: float = 5.0, gap: float = 3.0,
                   keep_clear: str = "none", pick_tilt: float = DEFAULT_PICK_TILT_DEG,
                   drop_tilt: float = DEFAULT_DROP_TILT_DEG) -> dict | None:
    """Maximise the worst joint margin on a deterministic radius/angle grid."""
    _finite_nonnegative(item_height=item_height, bin_width=bin_width, bin_height=bin_height,
                        clearance=clearance, gap=gap)
    _check_tilts(pick_tilt, drop_tilt)
    if bin_width == 0:
        raise ValueError("Bin width must be greater than zero.")
    if keep_clear not in ("left", "right", "none"):
        raise ValueError("Keep clear must be left, right, or none.")
    if len(pickup) != 2 or not all(math.isfinite(v) for v in pickup):
        raise ValueError("Pickup must have two finite coordinates in cm.")
    pickup_checks = model().inverse_many([(*pickup, item_height / 2), (*pickup, item_height + 5)],
                                         [pick_tilt, pick_tilt])
    if not all(result.reachable for result in pickup_checks):
        return None
    # Search arcs about base_link, not about the offset shoulder joint. Checking all
    # pairs also excludes overlap at the two arc ends. Grid includes rearward arcs.
    angles = np.arange(-180.0, 180.0, 5.0)
    unit = np.column_stack((np.cos(np.radians(angles)), np.sin(np.radians(angles))))
    candidates: list[tuple[float, tuple[int, ...], np.ndarray]] = []
    for radius in np.arange(10.0, 40.1, 2.0):
        ring = radius * unit
        for spacing in range(1, 24):
            for start in range(len(angles)):
                indices = tuple((start + i * spacing) % len(angles) for i in range(len(BINS)))
                points = ring[list(indices)]
                if _footprints_fit(points, pickup, bin_width, gap, keep_clear):
                    candidates.append((float(radius), indices, points))
    if not candidates:
        return None
    # Solve only distinct points used by geometrically feasible arcs.
    keys = sorted({(radius, index) for radius, indices, _ in candidates for index in indices})
    targets = [[*(radius * unit[index]), bin_height + clearance] for radius, index in keys]
    reach = dict(zip(keys, model().inverse_many(targets, [drop_tilt] * len(targets))))
    best: tuple[float, tuple[float, tuple[int, ...], np.ndarray]] | None = None
    for candidate in candidates:
        radius, indices, _ = candidate
        results = [reach[(radius, index)] for index in indices]
        if all(result.reachable for result in results):
            score = min(result.margin_deg for result in results)
            if best is None or score > best[0]:
                best = score, candidate
    if best is None:
        return None
    score, (radius, indices, points) = best
    bins = {name: {"x_cm": float(point[0]), "y_cm": float(point[1])}
            for name, point in zip(BINS, points)}
    return {
        "pickup": {"x_cm": float(pickup[0]), "y_cm": float(pickup[1]), "item_height_cm": item_height},
        "bins": bins,
        "bin_height_cm": bin_height, "clearance_cm": clearance,
        "bin_width_cm": bin_width, "gap_cm": gap, "keep_clear": keep_clear,
        "pick_tilt_deg": pick_tilt, "drop_tilt_deg": drop_tilt,
        "radius_cm": radius, "angular_spacing_deg": 5.0 * ((indices[1] - indices[0]) % len(angles)),
        "smallest_margin_deg": score,
        "search": "Best sampled arc: radius 10..40 cm by 2 cm, angles by 5 degrees.",
    }


def _table(rows: list[dict]) -> None:
    print(f"{'name':<17} {'x cm':>7} {'y cm':>7} {'z cm':>7} {'reachable':>10} {'tilt deg':>9} "
          f"{'margin deg':>11}")
    for row in rows:
        print(f"{row['name']:<17} {row['x_cm']:7.2f} {row['y_cm']:7.2f} {row['z_cm']:7.2f} "
              f"{'yes' if row['reachable'] else 'no':>10} {row['tilt_deg']:5.1f}/{row['max_tilt_deg']:<3.0f} "
              f"{row['margin_deg']:11.2f}")
    print("tilt = gripper angle from straight down / allowed. "
          "URDF angles (degrees; wrist_roll = 0, not LeRobot calibrated angles):")
    for row in rows:
        angles = ', '.join(f"{name}={value:.1f}" for name, value in row['urdf_angles_deg'].items())
        print(f"  {row['name']}: {angles}; position error {row['position_error_cm']:.3f} cm")


def _top_view(pickup: Sequence[float], bins: Mapping[str, Sequence[float]]) -> str:
    marks = [(0.0, 0.0, "B"), (*pickup, "X")]
    marks.extend((*bins[name], str(number)) for number, name in enumerate(BINS, 1))
    cells = [(round(x / 2), round(y / 2), symbol) for x, y, symbol in marks]
    xmin, xmax = min(x for x, _, _ in cells) - 1, max(x for x, _, _ in cells) + 1
    ymin, ymax = min(y for _, y, _ in cells) - 1, max(y for _, y, _ in cells) + 1
    grid = [["." for _ in range(xmax - xmin + 1)] for _ in range(ymax - ymin + 1)]
    for x, y, symbol in cells:
        grid[ymax - y][x - xmin] = symbol
    legend = ", ".join(f"{number}={name}" for number, name in enumerate(BINS, 1))
    return ("Top view: 2 cm/character; +x forward ->, +y left ^\n"
            + "\n".join("".join(row) for row in grid)
            + f"\nB=base, X=pickup, {legend}")


def _xy(raw: str) -> tuple[float, float]:
    try:
        x, y = map(float, raw.split(","))
        if not math.isfinite(x) or not math.isfinite(y):
            raise ValueError
        return x, y
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Use two finite cm coordinates, X,Y.") from exc


def _number(raw: str) -> float:
    try:
        value = float(raw)
        if not math.isfinite(value) or value < 0:
            raise ValueError
        return value
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Use a finite number, zero or more.") from exc


def main(argv: Sequence[str] | None = None) -> int:
    """Run a reach check or suggest a four-bin arc. Never connect to the arm."""
    arguments = list(sys.argv[1:] if argv is None else argv)
    print(NOTICE, file=sys.stderr if "--json" in arguments else sys.stdout)
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("check", help="Check pickup and four named bin centres in cm.")
    suggest = commands.add_parser("suggest", help="Find a reachable arc of four square bins.")
    check.add_argument("--pickup", type=_xy, required=True)
    check.add_argument("--bin", action="append", required=True, dest="bins", metavar="NAME=X,Y")
    suggest.add_argument("--pickup", type=_xy, default=(20.0, 0.0))
    suggest.add_argument("--bin-width", type=_number, default=15.0)
    suggest.add_argument("--gap", type=_number, default=3.0)
    suggest.add_argument("--keep-clear", choices=("left", "right", "none"), default="none")
    for command in (check, suggest):
        command.add_argument("--item-height", type=_number, default=6.0)
        command.add_argument("--bin-height", type=_number, default=15.0)
        command.add_argument("--clearance", type=_number, default=5.0)
        command.add_argument("--pick-tilt", type=_number, default=DEFAULT_PICK_TILT_DEG,
                             help="Allowed gripper angle from straight down at the pickup (degrees).")
        command.add_argument("--drop-tilt", type=_number, default=DEFAULT_DROP_TILT_DEG,
                             help="Allowed gripper angle from straight down over a bin (degrees).")
        command.add_argument("--json", action="store_true")
    args = parser.parse_args(arguments)
    try:
        if args.command == "check":
            bins = {}
            for entry in args.bins:
                name, separator, raw = entry.partition("=")
                if not separator or name not in BINS or name in bins:
                    raise ValueError(f"Supply each bin exactly once as NAME=X,Y: {', '.join(BINS)}.")
                bins[name] = _xy(raw)
            rows = check_layout(args.pickup, bins, args.item_height, args.bin_height, args.clearance,
                                args.pick_tilt, args.drop_tilt)
            ok = all(row["reachable"] for row in rows)
            if args.json:
                print(json.dumps({"reachable": ok, "points": rows}, indent=2))
            else:
                _table(rows)
            return 0 if ok else 1
        layout = suggest_layout(args.pickup, args.item_height, args.bin_width, args.bin_height,
                                args.clearance, args.gap, args.keep_clear,
                                args.pick_tilt, args.drop_tilt)
        if layout is None:
            if args.json:
                print(json.dumps({"pickup": {"x_cm": args.pickup[0], "y_cm": args.pickup[1]},
                                  "bins": {}, "error": "No reachable layout fits the sampled arcs."}))
            else:
                print("No reachable layout fits the sampled arcs. Try smaller bins, less height, or a different pickup.")
            return 1
        bins = {name: (point["x_cm"], point["y_cm"]) for name, point in layout["bins"].items()}
        rows = check_layout(args.pickup, bins, args.item_height, args.bin_height, args.clearance,
                                args.pick_tilt, args.drop_tilt)
        layout["points"] = rows
        if args.json:
            print(json.dumps(layout, indent=2))
        else:
            _table(rows)
            print(layout["search"])
            print(_top_view(args.pickup, bins))
        return 0 if all(row["reachable"] for row in rows) else 1
    except (ValueError, argparse.ArgumentTypeError) as exc:
        if args.json:
            print(json.dumps({"error": str(exc)}))
        else:
            print(f"Layout error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
