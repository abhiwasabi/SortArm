"""Teach-mode commands, without a console or hardware."""

from __future__ import annotations

import json

from arm.backend import MockBackend
from arm.poses import Poses, load
from arm.teach import HELP, TeachSession


def session(tmp_path, poses: Poses | None = None) -> TeachSession:
    start = {"shoulder_pan": 1.0, "shoulder_lift": 2.0, "elbow_flex": 3.0,
             "wrist_flex": 4.0, "wrist_roll": 5.0, "gripper": 40.0}
    return TeachSession(MockBackend(start=start), poses or Poses(), tmp_path / "poses.json")


def test_quit_words_quit_without_saving(tmp_path):
    s = session(tmp_path)
    for word in ("q", "quit", "exit", " QUIT "):
        assert s.handle(word) == ("", False)
    assert s.poses.poses == {}
    assert not (tmp_path / "poses.json").exists()


def test_help_shows_help_and_saves_nothing(tmp_path):
    s = session(tmp_path)
    assert s.handle("help") == (HELP, True)
    assert s.handle("?") == (HELP, True)
    assert s.poses.poses == {}


def test_saving_a_pose_and_the_gripper(tmp_path):
    s = session(tmp_path)
    message, keep_going = s.handle("home")
    assert keep_going and message.startswith("Saved home")
    s.handle("closed")
    saved = load(tmp_path / "poses.json")
    assert saved.poses["home"]["shoulder_lift"] == 2.0
    assert "gripper" not in saved.poses["home"]
    assert saved.gripper["closed"] == 40.0


def test_read_failure_is_reported_and_the_session_continues(tmp_path):
    s = session(tmp_path)
    s.backend.fail = ConnectionError
    message, keep_going = s.handle("home")
    assert keep_going
    assert "Could not read the arm" in message
    assert s.poses.poses == {}
    message, keep_going = s.handle("")
    assert keep_going and "Could not read the arm" in message


def test_list_marks_incomplete_poses_missing(tmp_path):
    incomplete = Poses(poses={"home": {"shoulder_pan": 1.0}}, gripper={"open": 50.0})
    text, _ = session(tmp_path, incomplete).handle("list")
    lines = [line.split() for line in text.splitlines()]
    assert ["home", "MISSING"] in lines
    assert ["gripper", "open", "50.0"] in lines
    assert ["gripper", "closed", "MISSING"] in lines


def test_torque_toggle(tmp_path):
    s = session(tmp_path)
    s.torque = True
    message, _ = s.handle("t")
    assert "OFF" in message and s.torque is False and s.backend.torque is False
    message, _ = s.handle("t")
    assert "ON" in message and s.torque is True


def test_saved_file_is_valid_json(tmp_path):
    s = session(tmp_path)
    s.handle("pick")
    data = json.loads((tmp_path / "poses.json").read_text(encoding="utf-8"))
    assert set(data) == {"poses", "gripper"}


def test_save_failure_is_reported_and_the_session_continues(tmp_path, monkeypatch):
    from arm import teach

    def locked(*args, **kwargs):
        raise PermissionError("file is locked by OneDrive")

    monkeypatch.setattr(teach, "save", locked)
    s = session(tmp_path)
    message, keep_going = s.handle("pick")
    assert keep_going
    assert "could not write" in message


def test_broken_poses_file_exits_before_opening_the_port(tmp_path, monkeypatch, capsys):
    from arm import teach

    bad = tmp_path / "poses.json"
    bad.write_text("<<<<<<< HEAD\n{}\n", encoding="utf-8")
    monkeypatch.setenv("ARM_POSES", str(bad))
    opened: list[object] = []
    monkeypatch.setattr(teach, "So101Backend", lambda config: opened.append(config))
    assert teach.main(["--port", "COM99"]) == 2
    assert opened == []
    assert "cannot read" in capsys.readouterr().err
