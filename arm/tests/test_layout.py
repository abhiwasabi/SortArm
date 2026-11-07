"""Offline tests for the SO-101 table layout planner."""

from __future__ import annotations

import json
import math

import numpy as np
import pytest

from arm.config import BINS
from arm.layout import (
    NOTICE,
    TILT_TOLERANCE_DEG,
    Kinematics,
    check_layout,
    main,
    model,
    suggest_layout,
)


def test_zero_pose() -> None:
    position, axis = Kinematics().forward(np.zeros(5))
    np.testing.assert_allclose(position, [0.391, 0.000, 0.226], atol=0.002)
    np.testing.assert_allclose(axis, [1.0, 0.0, 0.0], atol=0.06)


@pytest.mark.parametrize("pan,lift", [(-0.5, -0.7), (0.0, -0.4), (0.5, -0.1)])
def test_inverse_forward_round_trip(pan: float, lift: float) -> None:
    # Construct reachable, downward poses inside the supplied model's limits.
    # The flex joints share a pitch axis; cancel lift/elbow and turn the wrist down.
    _, zero_axis = model().forward(np.zeros(5))
    wrist = math.atan2(float(zero_axis[0]), -float(zero_axis[2]))
    known_q = np.array([pan, lift, -lift, wrist, 0.0])
    assert np.all(known_q >= model().limits[:, 0])
    assert np.all(known_q <= model().limits[:, 1])
    known_position, known_axis = model().forward(known_q)
    assert known_axis @ [0, 0, -1] > math.cos(math.radians(1))
    point = known_position * 100
    result = model().inverse(point)
    assert result.reachable, result
    q = np.radians([result.urdf_angles_deg[name] for name in model().names])
    position, axis = model().forward(q)
    assert np.linalg.norm(position * 100 - point) <= 0.5
    assert math.degrees(math.acos(float(np.clip(axis @ [0, 0, -1], -1, 1)))) <= 5 + TILT_TOLERANCE_DEG
    assert np.all(q >= model().limits[:, 0])
    assert np.all(q <= model().limits[:, 1])
    assert result.urdf_angles_deg["wrist_roll"] == 0
    expected_margin = np.degrees(np.min(np.minimum(q - model().limits[:, 0], model().limits[:, 1] - q)))
    assert result.margin_deg == pytest.approx(expected_margin)


def test_far_point_is_unreachable() -> None:
    assert not model().inverse((60, 0, 0)).reachable


@pytest.fixture(scope="module")
def default_layout() -> dict:
    layout = suggest_layout()
    assert layout is not None
    return layout


def test_suggested_layout_fits_and_checks(default_layout: dict) -> None:
    assert tuple(default_layout["bins"]) == BINS
    points = np.array([[default_layout["bins"][name]["x_cm"], default_layout["bins"][name]["y_cm"]]
                       for name in BINS])
    radii = np.linalg.norm(points, axis=1)
    np.testing.assert_allclose(radii, radii[0], atol=1e-8)
    angles = np.unwrap(np.arctan2(points[:, 1], points[:, 0]))
    np.testing.assert_allclose(np.diff(angles), np.diff(angles)[0], atol=1e-8)
    for first in range(4):
        assert np.linalg.norm(points[first] - [20, 0]) >= 15 / 2 + 3 - 1e-8
        for second in range(first):
            assert np.max(np.abs(points[first] - points[second])) >= 15 + 3 - 1e-8
    rows = check_layout((20, 0), dict(zip(BINS, points)))
    assert len(rows) == 6
    assert all(row["reachable"] for row in rows)
    assert rows[0]["z_cm"] == 3
    assert rows[1]["z_cm"] == 11
    assert all(row["z_cm"] == 20 for row in rows[2:])


def _bin_arguments(layout: dict) -> list[str]:
    arguments = []
    for name in BINS:
        point = layout["bins"][name]
        arguments.extend(["--bin", f"{name}={point['x_cm']},{point['y_cm']}"])
    return arguments


def test_cli_success_and_json(default_layout: dict, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["suggest", "--json"]) == 0
    output = capsys.readouterr()
    assert NOTICE in output.err
    suggestion = json.loads(output.out)
    assert tuple(suggestion["bins"]) == BINS
    assert main(["check", "--pickup", "20,0", *_bin_arguments(suggestion), "--json"]) == 0
    output = capsys.readouterr()
    assert NOTICE in output.err
    checked = json.loads(output.out)
    assert checked["reachable"]
    assert all(row["reachable"] for row in checked["points"])


def test_cli_table_and_top_view(default_layout: dict, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["suggest"]) == 0
    output = capsys.readouterr().out
    for text in (NOTICE, "URDF angles", "margin deg", "Top view", "2 cm/character", *BINS):
        assert text in output


def test_cli_unreachable(default_layout: dict, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["check", "--pickup", "60,0", *_bin_arguments(default_layout), "--json"]) == 1
    checked = json.loads(capsys.readouterr().out)
    assert not checked["reachable"]


@pytest.mark.parametrize("arguments", [
    ["suggest", "--bin-width", "100"],
    ["suggest", "--pickup", "60,0"],
    ["suggest", "--bin-height", "60"],
])
def test_cli_impossible(arguments: list[str], capsys: pytest.CaptureFixture[str]) -> None:
    assert main([*arguments, "--json"]) == 1
    output = capsys.readouterr()
    assert NOTICE in output.err
    assert "error" in json.loads(output.out)


@pytest.mark.parametrize("names", [BINS[:-1], (*BINS[:-1], "TRASH"), (*BINS, "PAPER")])
def test_cli_rejects_missing_unknown_and_duplicate_bins(names: tuple[str, ...], capsys) -> None:
    arguments = ["check", "--pickup", "20,0", "--json"]
    for name in names:
        arguments.extend(["--bin", f"{name}=20,10"])
    assert main(arguments) == 1
    assert "error" in json.loads(capsys.readouterr().out)


@pytest.mark.parametrize("bad", [(float("nan"), 0, 0), (1, 2), (float("inf"), 0, 0)])
def test_inverse_rejects_invalid_targets(bad: tuple[float, ...]) -> None:
    with pytest.raises(ValueError):
        model().inverse(bad)


@pytest.mark.parametrize("side", ["left", "right"])
def test_camera_side_checks_entire_footprints(side: str) -> None:
    from arm.layout import _footprints_fit

    sign = -1 if side == "left" else 1
    points = np.array([[0, 10], [20, 10], [40, 10], [60, 10]], dtype=float)
    points[:, 1] *= sign
    assert _footprints_fit(points, (-60, 0), 15, 3, side)
    points[0, 1] = sign * 5
    assert not _footprints_fit(points, (-60, 0), 15, 3, side)


def test_pickup_approach_height_is_reachable_with_a_small_tilt() -> None:
    # Brute force over the joint limits finds near-vertical poses here; four fixed seeds missed them.
    assert model().inverse((22, 0, 11), max_tilt_deg=15).reachable


def test_drop_height_needs_a_tilted_gripper() -> None:
    # With its wrist limits the SO-101 cannot point straight down at 20 cm above the table.
    assert model().inverse((25, 0, 20), max_tilt_deg=60).reachable
    assert not model().inverse((25, 0, 20), max_tilt_deg=10).reachable


def test_check_reports_tilt_per_point(default_layout: dict) -> None:
    bins = {name: (p["x_cm"], p["y_cm"]) for name, p in default_layout["bins"].items()}
    rows = check_layout((20, 0), bins, pick_tilt=15, drop_tilt=60)
    assert [row["max_tilt_deg"] for row in rows] == [15, 15, 60, 60, 60, 60]
    assert all(row["tilt_deg"] <= row["max_tilt_deg"] + TILT_TOLERANCE_DEG for row in rows)


def test_top_view_gives_every_mark_its_own_symbol(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["suggest"]) == 0
    output = capsys.readouterr().out
    assert "B=base, X=pickup, 1=PAPER, 2=PLASTIC, 3=METAL, 4=ORGANIC" in output
    view = output.split("Top view", 1)[1]
    for symbol in "BX1234":
        assert view.count(symbol) >= 2  # once on the map, once in the legend


@pytest.mark.parametrize("flag", ["--pick-tilt", "--drop-tilt"])
def test_tilt_must_be_between_0_and_90(flag: str, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["suggest", flag, "95", "--json"]) == 1
    assert "between 0 and 90" in json.loads(capsys.readouterr().out)["error"]
