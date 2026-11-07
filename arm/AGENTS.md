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
| `controller.py` | `ArmController`: the HOME/SORT/STOP state machine (worker thread for motion, notifier thread for callbacks), `from_config()` |
| `poses.py`, `poses.json` | Named poses; body joints in degrees, gripper `open`/`closed` stored separately |
| `teach.py` | Record poses by hand (torque off) |
| `cli.py` | Text-protocol console (stdout = state words only; stderr = `#` notes) |

## Invariants: do not break

1. A rejected command (wrong state, unknown bin, unknown text) never writes to the backend.
2. STOP is always accepted and freezes the arm (`motion.hold`): body goal = present position, gripper goal = its last command. It never disables torque. Each command has its own cancel event (`_Command.cancel`). STOP and command acceptance, and STOP and the worker's final transition, are decided under `_lock`. When `stop()` returns the state is ERROR, unless a newer command was accepted after the stopped one ended; `stop()` never overrides a command it did not stop. A command whose worker is still alive (hung bus) blocks new commands; a stopped command never resumes. Only HOME clears ERROR. The arm starts in ERROR ("not homed").
3. State order for an accepted command is BUSY → DONE → READY, or BUSY → ERROR. DONE's detail is the command string; the bridge counts items from it. Transitions happen only under `_lock` (`_transition`), and listeners get them in that order on the `arm-notifier` thread, never on the worker.
4. Moves stay interpolated at `speed_deg_s`, and LeRobot's `max_relative_target` cap stays on for body joints. `_settle` re-sends the goal only when it is within that cap, so the cap never becomes a fast move. The gripper is uncapped on purpose so it can keep squeezing; LeRobot limits its torque to 50%. `ArmConfig.validate()` refuses settings that would disable interpolation, and speeds above the ceilings in `config.py` (not settable from the environment).
5. Moves start from `ArmController._start_point()`: the gripper uses its last commanded value (a gripper stalled on an item keeps squeezing). A body joint uses its commanded goal only while it is within `max_relative_target` of the present position, else the present position (no jump to a stale goal after a power cut or push). Any connection loss clears `_goal`. A hold keeps the gripper's command.
6. stdout of `arm.cli` carries only `READY|BUSY|DONE|ERROR` lines, because other programs parse it.
7. `home()`/`sort()` never block. `stop()` never holds `_lock` while joining the worker. `poll()` holds `_bus_lock` while it reads, skips while BUSY, never raises, and discards its result if a command started meanwhile (`_cmd_seq`). Whether a command is still running is decided by `_Command.finished` (set under `_lock`), not by thread liveness. `close()` refuses new commands, stops motion, and takes `_bus_lock` before disconnecting. `last_rejection` is per thread.
8. Each completed command produces exactly one `DONE` notification and one `last_done` increment. A stopped or failed command produces neither.
9. Every backend write names all six joints (LeRobot's per-joint cap rejects partial goals). Only `ConnectionError` means "connection lost": `So101Backend` wraps LeRobot's `OSError`s as `ArmConnectionError`, and lets its `RuntimeError`s (servo errors, model mismatch) through, which are reported as "<command> failed" or "arm check failed".
10. `So101Backend` connects in this order: `bus.connect()`, calibration check, torque off, goal = present, `configure()`. Anything failing after the port opens closes it.

Any change to the public API, state machine, or text protocol is a contract change. Post it on the Message Board, update ARCHITECTURE.md, and add or adjust a test.

## Hardware-owned files

`poses.json` and `calibration/*.json` come from the physical arm. Do not hand-edit or regenerate them without the arm in front of you. They change only through `python -m arm.teach` and `lerobot-calibrate`.
