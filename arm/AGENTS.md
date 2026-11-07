# arm/ — agent guide

Read the root [AGENTS.md](../AGENTS.md) and [ARCHITECTURE.md](../ARCHITECTURE.md) first. Owner of this folder: @PaulPio (issue #20). Coordinate changes on the Message Board before editing here.

## Run

```sh
python -m pytest arm/tests        # contract tests, no hardware
python -m arm.cli --mock          # interactive console, no hardware
```

Use the repository-root `.venv` (Python 3.12, `pip install -r arm/requirements.txt`). Tests use `MockBackend` and must never need LeRobot or a serial port. Only `So101Backend` imports LeRobot, and it does so lazily.

## Modules

| File | Role |
| --- | --- |
| `config.py` | `ArmConfig` (env overrides), joint names, bins |
| `backend.py` | `So101Backend` (real arm via LeRobot `SO101Follower`), `MockBackend`, `Backend` protocol |
| `motion.py` | `move_to` (interpolated, stoppable, settle check), `hold`, `pause` |
| `controller.py` | `ArmController`: the HOME/SORT/STOP state machine on a worker thread |
| `poses.py`, `poses.json` | Named poses; body joints in degrees, gripper `open`/`closed` stored separately |
| `teach.py` | Record poses by hand (torque off) |
| `cli.py` | Text-protocol console (stdout = state words only; stderr = `#` notes) |

## Invariants: do not break

1. A rejected command (wrong state, unknown bin, unknown text) never writes to the backend.
2. STOP is always accepted. It holds position (goal = present), never disables torque, and leaves ERROR. Only HOME clears ERROR. The arm starts in ERROR ("not homed").
3. State order for an accepted command is BUSY → DONE → READY, or BUSY → ERROR. DONE's detail is the command string; the bridge counts items from it.
4. Moves stay interpolated at `speed_deg_s`, and LeRobot's `max_relative_target` cap stays on for body joints. The gripper is uncapped on purpose so it can keep squeezing; LeRobot limits its torque to 50%.
5. The controller commands the last goal, not the present position, for joints a step does not move. Otherwise a gripper stalled on an item would let go.
6. stdout of `arm.cli` carries only `READY|BUSY|DONE|ERROR` lines, because other programs parse it.
7. `home()`/`sort()` never block. `stop()` never takes the controller lock while waiting for the worker. `poll()` never touches the bus while a command holds `_bus_lock`.
8. Each completed command produces exactly one `DONE` notification and one `last_done` increment. A stopped or failed command produces neither.

Any change to the public API, state machine, or text protocol is a contract change. Post it on the Message Board, update ARCHITECTURE.md, and add or adjust a test.

## Hardware-owned files

`poses.json` and `calibration/*.json` come from the physical arm. Do not hand-edit or regenerate them without the arm in front of you. They change only through `python -m arm.teach` and `lerobot-calibrate`.
