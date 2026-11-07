"""Contract tests for ArmController, run against MockBackend (no hardware)."""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from arm.backend import MockBackend
from arm.cli import handle_line
from arm.config import BODY_JOINTS, ArmConfig
from arm.controller import ArmController, ArmState
from arm.poses import Poses, PosesError, demo_poses, load

POSES = demo_poses()


def fast_config(**overrides) -> ArmConfig:
    cfg = ArmConfig(
        speed_deg_s=5000.0,
        gripper_speed=5000.0,
        rate_hz=1000.0,
        settle_timeout_s=0.2,
        grip_dwell_s=0.0,
        release_dwell_s=0.0,
    )
    for key, value in overrides.items():
        setattr(cfg, key, value)
    return cfg


def make_arm(config: ArmConfig | None = None, **mock_kwargs) -> tuple[ArmController, MockBackend, list]:
    backend = MockBackend(**mock_kwargs)
    arm = ArmController(backend, POSES, config or fast_config())
    states: list[ArmState] = []
    arm.on_state(lambda state, detail: states.append(state))
    return arm, backend, states


def homed_arm(config: ArmConfig | None = None, **mock_kwargs):
    mock_kwargs.setdefault("start", {**POSES.body("home"), "gripper": POSES.grip("open")})
    arm, backend, states = make_arm(config, **mock_kwargs)
    assert arm.home()
    assert arm.wait_idle(2)
    assert arm.state is ArmState.READY
    states.clear()
    return arm, backend, states


def at(position: dict, pose: str, tol: float = 0.01) -> bool:
    body = POSES.body(pose)
    return all(abs(position[j] - body[j]) <= tol for j in BODY_JOINTS)


def test_starts_in_error_and_rejects_sort_until_homed():
    arm, backend, states = make_arm()
    assert arm.state is ArmState.ERROR
    assert not arm.sort("RECYCLE")
    assert "HOME" in arm.last_rejection
    assert backend.writes == []  # rejected commands never move the arm
    assert states == []


def test_home_reports_busy_done_ready_and_opens_gripper():
    arm, backend, states = make_arm()
    assert arm.home()
    assert arm.wait_idle(2)
    assert states == [ArmState.BUSY, ArmState.DONE, ArmState.READY]
    position = backend.read()
    assert at(position, "home")
    assert position["gripper"] == POSES.grip("open")


@pytest.mark.parametrize("bin_name", ["RECYCLE", "TRASH", "recycle"])
def test_sort_runs_full_sequence_and_returns_home(bin_name):
    arm, backend, states = homed_arm()
    assert arm.sort(bin_name)
    assert arm.wait_idle(2)
    assert states == [ArmState.BUSY, ArmState.DONE, ArmState.READY]

    # The item was gripped at the pickup spot and released over the right bin.
    closed, opened = POSES.grip("closed"), POSES.grip("open")
    above_bin = f"above_{bin_name.lower()}"
    assert any(at(w, "pick") and w["gripper"] == closed for w in backend.writes)
    assert any(at(w, above_bin) and w["gripper"] == closed for w in backend.writes)
    assert any(at(w, above_bin) and w["gripper"] == opened for w in backend.writes)
    assert at(backend.read(), "home")


def test_sort_plan_order():
    arm, _, _ = make_arm()
    poses_and_grips = [step for step in arm.sort_plan("TRASH") if step[0] != "pause"]
    assert poses_and_grips == [
        ("gripper", "open"),
        ("pose", "above_pick"),
        ("pose", "pick"),
        ("gripper", "closed"),
        ("pose", "above_pick"),
        ("pose", "above_trash"),
        ("gripper", "open"),
        ("pose", "home"),
    ]


def test_gripper_keeps_squeezing_while_carrying_an_item():
    # The gripper stalls on the item, so its present value never reaches "closed".
    # Later moves must keep commanding "closed", not the stalled present value.
    arm, backend, _ = homed_arm()
    backend.stalled.add("gripper")
    assert arm.sort("RECYCLE")
    assert arm.wait_idle(2)
    closed = POSES.grip("closed")
    carrying = [w for w in backend.writes if at(w, "above_recycle")]
    assert carrying and carrying[0]["gripper"] == closed


def test_unknown_bin_is_rejected_without_motion():
    arm, backend, states = homed_arm()
    writes_before = len(backend.writes)
    assert not arm.sort("GLASS")
    assert "unknown bin" in arm.last_rejection
    assert len(backend.writes) == writes_before
    assert states == []
    assert arm.state is ArmState.READY


def test_commands_while_busy_are_rejected():
    arm, _, _ = homed_arm(fast_config(speed_deg_s=50.0))  # slow enough to still be moving
    assert arm.sort("RECYCLE")
    assert arm.state is ArmState.BUSY
    assert not arm.sort("TRASH")
    assert not arm.home()
    arm.stop()
    assert arm.wait_idle(2)


def test_stop_mid_move_holds_position_and_requires_home():
    arm, backend, states = homed_arm(fast_config(speed_deg_s=50.0))
    moving = threading.Event()
    arm.on_state(lambda state, detail: moving.set() if state is ArmState.BUSY else None)
    assert arm.sort("TRASH")
    assert moving.wait(1)
    threading.Event().wait(0.2)  # let it travel part of the way

    assert arm.stop()
    assert arm.state is ArmState.ERROR
    assert arm.detail == "stopped"
    held = backend.read()
    assert backend.writes[-1] == held  # last command holds where the arm is
    assert not at(held, "home") and not at(held, "above_trash")

    writes_after_stop = len(backend.writes)
    threading.Event().wait(0.1)
    assert len(backend.writes) == writes_after_stop  # nothing else moves after STOP

    assert not arm.sort("TRASH")
    assert arm.home() and arm.wait_idle(2)
    assert arm.state is ArmState.READY


def test_stop_while_idle_enters_error():
    arm, backend, states = homed_arm()
    writes_before = len(backend.writes)
    assert arm.stop()
    assert arm.state is ArmState.ERROR
    assert states == [ArmState.ERROR]
    assert len(backend.writes) == writes_before
    assert not arm.sort("RECYCLE")


def test_joint_that_cannot_reach_its_goal_reports_error():
    arm, backend, states = make_arm(stalled={"shoulder_lift"})
    assert arm.home()
    assert arm.wait_idle(2)
    assert arm.state is ArmState.ERROR
    assert "shoulder_lift" in arm.detail
    assert states == [ArmState.BUSY, ArmState.ERROR]


def test_home_and_sort_return_immediately():
    arm, _, _ = make_arm(fast_config(speed_deg_s=50.0))
    t0 = time.monotonic()
    assert arm.home()
    assert time.monotonic() - t0 < 0.1
    assert arm.state is ArmState.BUSY
    arm.stop()


def test_each_completed_command_is_reported_once():
    arm, _, _ = homed_arm()
    done: list[str] = []
    arm.on_state(lambda state, detail: done.append(detail) if state is ArmState.DONE else None)
    seq_after_home = arm.last_done[0]

    assert arm.sort("RECYCLE") and arm.wait_idle(2)
    assert arm.sort("TRASH") and arm.wait_idle(2)
    assert done == ["SORT RECYCLE", "SORT TRASH"]
    assert arm.last_done == (seq_after_home + 2, "SORT TRASH")


def test_stopped_sort_is_not_reported_done():
    arm, _, _ = homed_arm(fast_config(speed_deg_s=50.0))
    before = arm.last_done
    done: list[str] = []
    arm.on_state(lambda state, detail: done.append(detail) if state is ArmState.DONE else None)
    assert arm.sort("RECYCLE")
    time.sleep(0.1)
    arm.stop()
    assert done == []
    assert arm.last_done == before


def test_poll_reports_connection_loss_and_recovery():
    arm, backend, states = homed_arm()
    assert arm.poll() and arm.connected

    backend.fail = True
    assert not arm.poll()
    assert not arm.connected
    assert arm.state is ArmState.ERROR
    assert arm.detail.startswith("connection lost")
    assert not arm.sort("RECYCLE")

    backend.fail = False
    assert arm.poll() and arm.connected
    assert arm.state is ArmState.ERROR  # still needs an operator HOME
    assert arm.home() and arm.wait_idle(2)
    assert arm.state is ArmState.READY


def test_connection_lost_during_a_move():
    arm, backend, _ = homed_arm(fast_config(speed_deg_s=50.0))
    assert arm.sort("TRASH")
    time.sleep(0.1)
    backend.fail = True
    assert arm.wait_idle(2)
    assert arm.state is ArmState.ERROR
    assert not arm.connected
    assert arm.detail.startswith("connection lost")


def test_poll_does_not_touch_the_bus_during_a_move():
    arm, backend, _ = homed_arm(fast_config(speed_deg_s=50.0))
    assert arm.sort("RECYCLE")
    time.sleep(0.05)
    backend.fail = True  # a poll that read the bus would now fail
    assert arm.poll()
    backend.fail = False
    arm.stop()


def test_untaught_poses_are_refused():
    template = load(Path(__file__).resolve().parents[1] / "poses.json")
    assert "home" in template.missing()
    with pytest.raises(PosesError):
        ArmController(MockBackend(), template, fast_config())
    with pytest.raises(PosesError):
        ArmController(MockBackend(), Poses(poses=POSES.poses, gripper={"open": 50.0}), fast_config())


def test_text_protocol_lines():
    arm, _, _ = make_arm()
    assert handle_line(arm, "SORT RECYCLE") is not None  # not homed yet
    assert handle_line(arm, "home") is None
    assert arm.wait_idle(2)
    assert handle_line(arm, "SORT GLASS").startswith("unknown bin")
    assert handle_line(arm, "DANCE").startswith("unknown command")
    assert handle_line(arm, "") is None
    assert handle_line(arm, "sort trash") is None
    assert arm.wait_idle(2)
    assert handle_line(arm, "STOP") is None
    assert arm.state is ArmState.ERROR
