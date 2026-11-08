"""Contract tests for ArmController, run against MockBackend (no hardware)."""

from __future__ import annotations

import random
import threading
import time
from pathlib import Path

import pytest

from arm import cli
from arm import controller as controller_module
from arm.backend import MockBackend
from arm.cli import handle_line
from arm.config import BODY_JOINTS, ArmConfig
from arm.controller import ArmController, ArmState
from arm.motion import MotionError, max_catchup, move_to
from arm.poses import Poses, PosesError, demo_poses, load, save

POSES = demo_poses()
OPEN, CLOSED = POSES.grip("open"), POSES.grip("closed")
HOME_START = {**POSES.body("home"), "gripper": OPEN}


def fast_config(**overrides) -> ArmConfig:
    """Fast settings so the mock tests run quickly. The safety ceilings are lifted for the mock only."""
    cfg = ArmConfig(
        speed_deg_s=5000.0,
        gripper_speed=5000.0,
        rate_hz=1000.0,
        settle_timeout_s=0.2,
        grip_dwell_s=0.0,
        release_dwell_s=0.0,
        speed_limit_deg_s=1e7,
        gripper_speed_limit=1e7,
        relative_target_limit=1e7,
    )
    for key, value in overrides.items():
        setattr(cfg, key, value)
    return cfg


def make_arm(config: ArmConfig | None = None, **mock_kwargs):
    backend = MockBackend(**mock_kwargs)
    arm = ArmController(backend, POSES, config or fast_config())
    states: list[ArmState] = []
    arm.on_state(lambda state, detail: states.append(state))
    return arm, backend, states


def homed_arm(config: ArmConfig | None = None, **mock_kwargs):
    mock_kwargs.setdefault("start", dict(HOME_START))
    arm, backend, states = make_arm(config, **mock_kwargs)
    assert arm.home()
    assert arm.wait_idle(2)
    assert arm.state is ArmState.READY
    states.clear()
    return arm, backend, states


def at(position: dict, pose: str, tol: float = 0.01) -> bool:
    body = POSES.body(pose)
    return all(abs(position[j] - body[j]) <= tol for j in BODY_JOINTS)


def wait_for(predicate, timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.002)
    return False


# ----- basic contract ---------------------------------------------------------------------------


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
    assert position["gripper"] == OPEN


@pytest.mark.parametrize("bin_name", ["RECYCLE", "TRASH", "recycle"])
def test_sort_runs_full_sequence_and_returns_home(bin_name):
    arm, backend, states = homed_arm()
    assert arm.sort(bin_name)
    assert arm.wait_idle(2)
    assert states == [ArmState.BUSY, ArmState.DONE, ArmState.READY]
    above_bin = f"above_{bin_name.lower()}"
    assert any(at(w, "pick") and w["gripper"] == CLOSED for w in backend.writes)
    assert any(at(w, above_bin) and w["gripper"] == CLOSED for w in backend.writes)
    assert any(at(w, above_bin) and w["gripper"] == OPEN for w in backend.writes)
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


def test_unknown_bin_is_rejected_without_motion():
    arm, backend, states = homed_arm()
    writes_before = len(backend.writes)
    assert not arm.sort("GLASS")
    assert "unknown bin" in arm.last_rejection
    assert len(backend.writes) == writes_before
    assert states == []
    assert arm.state is ArmState.READY


def test_home_and_sort_return_immediately():
    arm, _, _ = make_arm(fast_config(speed_deg_s=50.0))
    t0 = time.monotonic()
    assert arm.home()
    assert time.monotonic() - t0 < 0.1
    assert arm.state is ArmState.BUSY
    arm.stop()


def test_commands_while_busy_are_rejected():
    arm, _, _ = homed_arm(fast_config(speed_deg_s=50.0))
    assert arm.sort("RECYCLE")
    assert arm.state is ArmState.BUSY
    assert not arm.sort("TRASH")
    assert not arm.home()
    arm.stop()
    assert arm.wait_idle(2)


# ----- gripper keeps squeezing (findings #1, #2) -----------------------------------------------


def grasp_and_release_indexes(writes: list[dict], bin_pose: str) -> tuple[int, int]:
    grasp = next(i for i, w in enumerate(writes) if at(w, "pick") and w["gripper"] == CLOSED)
    release = next(i for i, w in enumerate(writes) if i > grasp and at(w, bin_pose) and w["gripper"] != CLOSED)
    return grasp, release


def test_every_carry_write_commands_closed_when_gripper_is_stalled():
    # The gripper stalls on the item, so its present value never reaches "closed". Every goal sent
    # between the grasp and the release must still say "closed", or the grip loosens.
    arm, backend, _ = homed_arm(fast_config(speed_deg_s=500.0))
    backend.stalled.add("gripper")
    assert arm.sort("RECYCLE")
    assert arm.wait_idle(3)
    grasp, release = grasp_and_release_indexes(backend.writes, "above_recycle")
    assert all(w["gripper"] == CLOSED for w in backend.writes[grasp:release])


def test_stop_while_carrying_keeps_the_gripper_closed():
    arm, backend, _ = homed_arm(fast_config(speed_deg_s=40.0))
    backend.stalled.add("gripper")  # stalled at "open": commanding the present value would let go
    assert arm.sort("RECYCLE")
    assert wait_for(lambda: any(at(w, "pick") and w["gripper"] == CLOSED for w in backend.writes))
    time.sleep(0.3)  # carrying back up toward above_pick
    assert arm.stop()
    held = backend.writes[-1]
    assert held["gripper"] == CLOSED
    assert all(held[j] == backend.read()[j] for j in BODY_JOINTS)

    # HOME afterwards starts from the held goal: no write before the release relaxes the grip.
    assert arm.home() and arm.wait_idle(5)
    after_stop = backend.writes[backend.writes.index(held):]
    first_open = next(i for i, w in enumerate(after_stop) if w["gripper"] != CLOSED)
    assert at(after_stop[first_open - 1], "home")


# ----- STOP (findings #3, #4) -------------------------------------------------------------------


def test_stop_mid_move_holds_position_and_requires_home():
    arm, backend, states = homed_arm(fast_config(speed_deg_s=50.0))
    assert arm.sort("TRASH")
    time.sleep(0.2)

    assert arm.stop()
    assert arm.state is ArmState.ERROR
    assert arm.detail == "stopped"
    held = backend.read()
    assert backend.writes[-1] == held
    assert not at(held, "home") and not at(held, "above_trash")

    writes_after_stop = len(backend.writes)
    time.sleep(0.1)
    assert len(backend.writes) == writes_after_stop

    assert not arm.sort("TRASH")
    assert arm.home() and arm.wait_idle(5)
    assert arm.state is ArmState.READY


def test_stop_while_idle_enters_error():
    arm, backend, states = homed_arm()
    writes_before = len(backend.writes)
    assert arm.stop()
    assert arm.state is ArmState.ERROR
    assert arm.wait_idle(1)
    assert states == [ArmState.ERROR]
    assert len(backend.writes) == writes_before
    assert not arm.sort("RECYCLE")
    assert arm.stop() and arm.wait_idle(1)
    assert states == [ArmState.ERROR]  # a second STOP reports nothing new


def test_stop_racing_a_new_command_always_ends_in_error():
    for _ in range(200):
        arm, _, _ = homed_arm()
        barrier = threading.Barrier(2)

        def do_sort():
            barrier.wait()
            arm.sort("RECYCLE")

        sorter = threading.Thread(target=do_sort, daemon=True)
        sorter.start()
        barrier.wait()
        arm.stop()
        sorter.join()
        assert arm.wait_idle(2)
        assert arm.state is ArmState.ERROR
        arm.close()


def test_stop_near_the_end_of_a_command_always_ends_in_error():
    rng = random.Random(7)
    for _ in range(100):
        arm, _, _ = homed_arm()
        done_before = arm.last_done
        assert arm.sort("TRASH")
        time.sleep(rng.uniform(0.0, 0.02))
        arm.stop()
        assert arm.state is ArmState.ERROR  # even if the sort had already finished
        assert arm.wait_idle(2)
        assert arm.state is ArmState.ERROR
        assert arm.last_done is None or arm.last_done[0] <= done_before[0] + 1
        arm.close()


# ----- completion reporting ---------------------------------------------------------------------


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
    assert arm.wait_idle(2)
    assert done == []
    assert arm.last_done == before


# ----- listeners (finding #11) ------------------------------------------------------------------


def test_listener_can_chain_the_next_sort():
    arm, _, _ = homed_arm()
    accepted: list[bool] = []
    done: list[str] = []

    def listener(state, detail):
        if state is ArmState.DONE:
            done.append(detail)
        if state is ArmState.READY and not accepted:
            accepted.append(arm.sort("TRASH"))

    arm.on_state(listener)
    assert arm.sort("RECYCLE")
    assert arm.wait_idle(2)
    assert arm.wait_idle(2)  # the chained sort started from the listener
    assert accepted == [True]
    assert done == ["SORT RECYCLE", "SORT TRASH"]


def test_listener_stop_reports_a_single_error():
    arm, _, states = homed_arm(fast_config(speed_deg_s=50.0))
    arm.on_state(lambda state, detail: arm.stop() if state is ArmState.BUSY else None)
    assert arm.sort("RECYCLE")
    assert wait_for(lambda: arm.state is ArmState.ERROR)
    assert arm.wait_idle(2)
    assert states == [ArmState.BUSY, ArmState.ERROR]
    assert arm.detail == "stopped"


def test_listeners_never_run_on_the_worker_thread():
    arm, _, _ = homed_arm()
    threads: set[str] = set()
    arm.on_state(lambda state, detail: threads.add(threading.current_thread().name))
    assert arm.sort("RECYCLE") and arm.wait_idle(2)
    assert threads == {"arm-notifier"}


# ----- faults, connection, poll (findings #7, #9, #10, #15) ------------------------------------


def test_joint_that_cannot_reach_its_goal_reports_error():
    arm, backend, states = make_arm(stalled={"shoulder_lift"})
    assert arm.home()
    assert arm.wait_idle(2)
    assert arm.state is ArmState.ERROR
    assert "shoulder_lift" in arm.detail
    assert arm.connected  # the arm answered; this is not a lost connection
    assert states == [ArmState.BUSY, ArmState.ERROR]


def test_settle_resends_a_slightly_clipped_goal():
    # The arm lagged: the final goal (15 degrees away) was clipped to 10. Once the arm catches up to
    # within the cap, settling re-sends the goal and the move completes.
    backend = MockBackend(max_step=10.0)
    goal = move_to(backend, None, {"shoulder_lift": -15.0}, fast_config(speed_deg_s=1e6, gripper_speed=1e6),
                   threading.Event())
    assert goal["shoulder_lift"] == -15.0
    assert backend.read()["shoulder_lift"] == -15.0


def test_settle_never_drives_a_far_goal_through_the_cap():
    # A goal far beyond the cap (a bad pose, or a stale target) is clipped once and NOT re-sent, so the
    # cap does not become a fast move: the arm stops 10 degrees along and the move fails, holding.
    backend = MockBackend(max_step=10.0)
    with pytest.raises(MotionError):
        move_to(backend, None, {"shoulder_lift": -90.0}, fast_config(speed_deg_s=1e6, gripper_speed=1e6),
                threading.Event())
    assert backend.read()["shoulder_lift"] == -10.0


def test_non_transport_error_is_not_reported_as_connection_lost():
    arm, backend, _ = homed_arm()
    backend.fail = ZeroDivisionError
    assert arm.home()
    assert arm.wait_idle(2)
    assert arm.state is ArmState.ERROR
    assert arm.detail.startswith("HOME failed")
    assert arm.connected


def test_poll_reports_connection_loss_and_recovery():
    arm, backend, _ = homed_arm()
    assert arm.poll() and arm.connected

    backend.fail = ConnectionError
    assert not arm.poll()
    assert not arm.connected
    assert arm.state is ArmState.ERROR
    assert arm.detail.startswith("connection lost")
    assert not arm.sort("RECYCLE")

    backend.fail = None
    assert arm.poll() and arm.connected
    assert arm.state is ArmState.ERROR  # still needs an operator HOME
    assert arm.home() and arm.wait_idle(2)
    assert arm.state is ArmState.READY


def test_connection_lost_during_a_move():
    arm, backend, _ = homed_arm(fast_config(speed_deg_s=50.0))
    assert arm.sort("TRASH")
    time.sleep(0.1)
    backend.fail = ConnectionError
    assert arm.wait_idle(2)
    assert arm.state is ArmState.ERROR
    assert not arm.connected
    assert arm.detail.startswith("connection lost")


def test_poll_during_a_move_does_not_touch_the_bus():
    arm, backend, states = homed_arm(fast_config(speed_deg_s=50.0))
    assert arm.sort("RECYCLE")
    me = threading.current_thread().name
    for _ in range(20):
        assert arm.poll()
    assert me not in backend.read_threads
    assert arm.state is ArmState.BUSY
    arm.stop()


# ----- settings, construction, console (findings #6, #8, #12, #13) ----------------------------


@pytest.mark.parametrize("field, value", [
    ("speed_deg_s", -20.0), ("speed_deg_s", 0.0), ("gripper_speed", 0.0), ("rate_hz", float("nan")),
    ("settle_timeout_s", 0.0), ("grip_dwell_s", -1.0),
])
def test_bad_settings_are_refused(field, value):
    with pytest.raises(ValueError):
        ArmController(MockBackend(), POSES, fast_config(**{field: value}))


def test_partial_goal_is_refused():
    with pytest.raises(ValueError):
        MockBackend().write({"gripper": 10.0})


def test_untaught_poses_are_refused():
    template = load(Path(__file__).resolve().parents[1] / "poses.json")
    assert "home" in template.missing()
    with pytest.raises(PosesError):
        ArmController(MockBackend(), template, fast_config())
    with pytest.raises(PosesError):
        ArmController(MockBackend(), Poses(poses=POSES.poses, gripper={"open": 50.0}), fast_config())


def test_from_config_checks_poses_before_opening_the_port(tmp_path, monkeypatch):
    opened: list[object] = []
    monkeypatch.setattr(controller_module, "So101Backend", lambda config: opened.append(config))
    config = fast_config(poses_path=tmp_path / "poses.json", port="COM99")
    save(Poses(), config.poses_path)
    with pytest.raises(PosesError):
        ArmController.from_config(config)
    assert opened == []


def test_from_config_closes_the_backend_if_the_controller_fails(tmp_path, monkeypatch):
    backend = MockBackend()
    monkeypatch.setattr(controller_module, "So101Backend", lambda config: backend)
    config = fast_config(poses_path=tmp_path / "poses.json", port="COM99")
    save(POSES, config.poses_path)

    def boom(self, *args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(ArmController, "__init__", boom)
    with pytest.raises(RuntimeError):
        ArmController.from_config(config)
    assert backend.closed


def test_from_config_mock_uses_demo_poses_when_untaught(tmp_path):
    config = fast_config(poses_path=tmp_path / "poses.json")
    arm = ArmController.from_config(config, mock=True)
    assert arm.home() and arm.wait_idle(2)
    assert arm.state is ArmState.READY
    arm.close()


def test_cli_reports_a_bad_port_and_exits_2(monkeypatch, capsys):
    def no_port(config, mock=False):
        raise ConnectionError("Could not connect on port 'COM99'")

    monkeypatch.setattr(cli.ArmController, "from_config", staticmethod(no_port))
    assert cli.main(["--port", "COM99"]) == 2
    assert "# Could not connect on port 'COM99'" in capsys.readouterr().err


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


# ----- second review round ----------------------------------------------------------------------


def test_a_hung_stopped_command_never_resumes():
    # A write hangs mid-sort. STOP gives up waiting ("arm not responding"). HOME must be refused
    # while the stopped worker is alive, and when the bus recovers the old SORT must not continue.
    config = fast_config(speed_deg_s=50.0, settle_timeout_s=0.1)
    arm, backend, _ = homed_arm(config)
    done: list[str] = []
    arm.on_state(lambda state, detail: done.append(detail) if state is ArmState.DONE else None)
    assert arm.sort("RECYCLE")
    time.sleep(0.1)
    backend.write_gate = threading.Event()  # the bus hangs

    assert arm.stop()
    assert arm.state is ArmState.ERROR
    assert arm.detail == "stopped; arm not responding"
    assert not arm.home()
    assert "not ended" in arm.last_rejection

    gate, backend.write_gate = backend.write_gate, None
    gate.set()  # the bus recovers
    assert arm.wait_idle(2)
    assert arm.state is ArmState.ERROR
    assert arm.home() and arm.wait_idle(5)
    assert arm.state is ArmState.READY
    assert done == ["HOME"]  # the stopped SORT never completed or counted


def test_after_a_power_cut_moves_start_from_where_the_arm_really_is():
    config = fast_config(speed_deg_s=600.0)
    arm, backend, _ = homed_arm(config, max_step=10.0)
    backend.fail = ConnectionError  # servo power cut
    assert not arm.poll()
    backend.move_by_hand("shoulder_lift", -30.0)  # the arm sags 60 degrees
    backend.fail = None
    assert arm.poll()

    writes_before = len(backend.writes)
    assert arm.home() and arm.wait_idle(5)
    assert arm.state is ArmState.READY
    first = backend.writes[writes_before]
    assert abs(first["shoulder_lift"] - (-30.0)) < 2.0  # no jump back to the old -90 goal


def test_close_refuses_new_commands_and_stops_motion():
    arm, backend, _ = homed_arm(fast_config(speed_deg_s=50.0))
    assert arm.sort("TRASH")
    time.sleep(0.1)
    arm.close()
    assert backend.closed
    assert arm.state is ArmState.ERROR
    writes = len(backend.writes)
    time.sleep(0.1)
    assert len(backend.writes) == writes  # nothing moves after close
    assert not arm.home()
    assert "closed" in arm.last_rejection
    assert arm.wait_idle(1)  # does not hang after close
    assert not arm.poll()


def test_close_waits_for_a_command_chained_from_a_listener():
    arm, backend, _ = homed_arm()
    arm.on_state(lambda state, detail: arm.sort("TRASH") if state is ArmState.READY else None)
    assert arm.sort("RECYCLE")
    time.sleep(0.005)
    arm.close()
    closed_at = len(backend.writes)
    time.sleep(0.05)
    assert len(backend.writes) == closed_at  # no worker kept writing after close


def test_stop_does_not_override_a_command_accepted_after_it():
    arm, _, states = homed_arm(fast_config(speed_deg_s=50.0))
    homes: list[bool] = []
    arm.on_state(lambda state, detail: homes.append(arm.home())
                 if state is ArmState.ERROR and detail == "stopped" and not homes else None)
    assert arm.sort("RECYCLE")
    time.sleep(0.05)
    arm.stop()
    assert wait_for(lambda: bool(homes))
    assert homes == [True]
    assert arm.wait_idle(5)
    assert arm.state is ArmState.READY
    assert states[-3:] == [ArmState.BUSY, ArmState.DONE, ArmState.READY]


@pytest.mark.parametrize("error", [KeyError, ValueError, RuntimeError])
def test_poll_never_raises(error):
    arm, backend, _ = homed_arm()
    backend.fail = error
    assert arm.poll()  # not a connection failure: still connected
    assert arm.state is ArmState.ERROR
    assert arm.detail.startswith("arm check failed")


def test_stale_poll_result_never_overrides_a_newer_command():
    arm, backend, states = homed_arm()
    backend.read_gate = threading.Event()
    backend.fail_once = ConnectionError
    poller = threading.Thread(target=arm.poll, daemon=True)
    poller.start()
    time.sleep(0.05)  # poll is now blocked inside its read
    assert arm.home()  # accepted; its worker waits for the bus
    backend.read_gate.set()  # poll's read fails with a stale glitch
    poller.join(2)
    assert arm.wait_idle(2)
    assert arm.connected
    assert arm.state is ArmState.READY
    assert ArmState.ERROR not in states


@pytest.mark.parametrize("field, value", [
    ("speed_deg_s", 2000.0), ("gripper_speed", 5000.0), ("max_relative_target", 1000.0),
])
def test_settings_above_the_safety_ceiling_are_refused(field, value):
    with pytest.raises(ValueError, match="safety ceiling"):
        ArmConfig(**{field: value}).validate()
    ArmConfig().validate()  # the defaults are fine


def test_states_print_as_bare_names():
    assert str(ArmState.READY) == "READY"
    assert f"{ArmState.BUSY}" == "BUSY"
    assert ArmState.DONE == "DONE"


def test_rejection_reasons_are_per_thread():
    arm, _, _ = homed_arm(fast_config(speed_deg_s=50.0))
    other: list[str | None] = []
    rejected = threading.Event()
    accepted = threading.Event()

    def other_thread():
        arm.sort("GLASS")
        rejected.set()
        accepted.wait(2)
        other.append(arm.last_rejection)

    t = threading.Thread(target=other_thread, daemon=True)
    t.start()
    rejected.wait(2)
    assert arm.sort("RECYCLE")  # this thread's command is accepted
    accepted.set()
    t.join(2)
    assert arm.last_rejection is None
    assert other and other[0].startswith("unknown bin")
    arm.stop()


def test_console_polls_and_reports_a_lost_connection():
    arm, backend, states = homed_arm()
    stop_polling = cli.start_polling(arm, interval_s=0.02)
    try:
        backend.fail = ConnectionError
        assert wait_for(lambda: arm.state is ArmState.ERROR, timeout=2)
        assert arm.detail.startswith("connection lost")
    finally:
        stop_polling.set()


def test_note_formatter_prefixes_every_line():
    import logging
    record = logging.LogRecord("arm", logging.WARNING, __file__, 1, "first\nsecond\n\nthird", None, None)
    lines = cli.NoteFormatter().format(record).splitlines()
    assert lines == ["# first", "# second", "# third"]


# ----- four material bins (issue #23) ------------------------------------------------------------


def test_the_two_trial_bins_and_their_required_poses():
    from arm.config import BINS
    from arm.poses import REQUIRED_POSES

    assert BINS == ("RECYCLE", "TRASH")
    for name in ("above_recycle", "above_trash"):
        assert name in REQUIRED_POSES
    template = load(Path(__file__).resolve().parents[1] / "poses.json")
    assert set(template.poses) == set(REQUIRED_POSES)


@pytest.mark.parametrize("old_bin", ["PAPER", "PLASTIC", "METAL", "ORGANIC"])
def test_the_four_material_names_are_rejected_without_motion(old_bin):
    arm, backend, _ = homed_arm()
    writes = len(backend.writes)
    assert not arm.sort(old_bin)
    assert "unknown bin" in arm.last_rejection
    assert len(backend.writes) == writes


# ----- PR #25 follow-ups (Codex review comments) -------------------------------------------------


def test_fault_mid_close_freezes_the_gripper_where_it_was_sent_not_reopened():
    # A servo error strikes part-way through closing on an item. The freeze must keep the last
    # gripper value actually sent, not reopen to the previous move's goal ("open").
    arm, backend, _ = homed_arm(fast_config(gripper_speed=200.0))
    sent: list[float] = []
    failed = {"yet": False}

    def hook(goal):
        if failed["yet"] or not (at(goal, "pick") and CLOSED < goal["gripper"] < OPEN):
            return
        if len(sent) == 3:
            failed["yet"] = True
            raise RuntimeError("servo 6 refused the write")
        sent.append(goal["gripper"])

    backend.write_hook = hook
    assert arm.sort("RECYCLE")
    assert arm.wait_idle(3)
    assert arm.state is ArmState.ERROR
    assert arm.detail.startswith("SORT RECYCLE failed")
    freeze = backend.writes[-1]
    assert freeze["gripper"] == sent[-1]
    assert CLOSED < freeze["gripper"] < OPEN


def test_close_never_disconnects_while_a_hung_operation_holds_the_bus():
    # Codex's reproduction: the bus hangs mid-move, so close() cannot get it in time. It must not
    # disconnect underneath the stuck write; the worker disconnects when it ends.
    arm, backend, _ = homed_arm(fast_config(speed_deg_s=50.0, settle_timeout_s=0.1))
    assert arm.sort("RECYCLE")
    time.sleep(0.05)
    gate = threading.Event()
    backend.write_gate = gate
    try:
        assert arm.close(timeout=0.2) is False
        assert not backend.closed
    finally:
        backend.write_gate = None
        gate.set()  # the stuck write completes; the cancelled worker holds, reports, then disconnects
    assert wait_for(lambda: backend.closed, timeout=3)
    assert backend.writes_after_close == 0
    assert arm.state is ArmState.ERROR
    assert not arm.home()


def test_close_waits_for_a_hung_poll_then_disconnects():
    arm, backend, _ = homed_arm()
    gate = threading.Event()
    backend.read_gate = gate
    poller = threading.Thread(target=arm.poll, daemon=True)
    poller.start()
    time.sleep(0.05)  # poll now holds the bus, stuck in its read
    try:
        assert arm.close(timeout=0.2) is False
        assert not backend.closed
    finally:
        backend.read_gate = None
        gate.set()
    poller.join(2)
    assert backend.closed
    assert backend.writes_after_close == 0


def test_idle_close_disconnects_at_once():
    arm, backend, states = homed_arm()
    assert arm.stop()
    t0 = time.monotonic()
    assert arm.close() is True
    assert time.monotonic() - t0 < 1.5
    assert backend.closed
    assert states == [ArmState.ERROR]  # delivered before close() returned
    assert not arm.poll()
    assert arm.close() is True  # a second close reports the same


def test_a_delayed_write_does_not_cause_a_catch_up_burst():
    # One bus write takes 150 ms. Afterwards the move must carry on at its normal pace: no burst of
    # overdue steps back-to-back, and never faster than speed_deg_s.
    period, speed = 0.01, 100.0
    config = fast_config(speed_deg_s=speed, rate_hz=1 / period)
    backend = MockBackend()
    count = {"writes": 0}

    def hook(goal):
        count["writes"] += 1
        if count["writes"] == 5:
            time.sleep(0.15)

    backend.write_hook = hook
    move_to(backend, None, {"shoulder_lift": -30.0}, config, threading.Event())

    times, lifts = backend.write_times, [w["shoulder_lift"] for w in backend.writes]
    gaps = [b - a for a, b in zip(times, times[1:])]
    assert lifts[-1] == -30.0
    assert min(gaps) >= 0.8 * period  # no back-to-back burst after the delay
    for (a, b), gap in zip(zip(lifts, lifts[1:]), gaps):
        assert abs(b - a) <= speed * gap * 1.05  # never faster than speed_deg_s
        assert abs(b - a) <= speed * max_catchup(period) + 1e-9  # bounded step, even after the stall


def test_consecutive_moves_never_send_back_to_back_writes():
    # The arm is already within tolerance after a move's final write, so settling returns at once.
    # The next move's first write must still come a full period after that final write.
    period = 0.01
    config = fast_config(speed_deg_s=100.0, rate_hz=1 / period)
    backend = MockBackend()
    goal = move_to(backend, None, {"shoulder_lift": -10.0}, config, threading.Event())
    move_to(backend, goal, {**goal, "shoulder_lift": -20.0}, config, threading.Event())
    gaps = [b - a for a, b in zip(backend.write_times, backend.write_times[1:])]
    assert min(gaps) >= 0.8 * period
