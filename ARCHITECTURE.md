# Architecture

One-page map of the SharkByte 2026 robotic waste sorter, for teammates and their AI agents. [PLAN.md](PLAN.md) has the goals and build order, and [AGENTS.md](AGENTS.md) has the team rules. Each issue holds its own task details. When this file and an issue disagree, ask on the [Message Board](https://github.com/abhiwasabi/sharkbyte-2026/issues/1) and fix whichever is wrong.

## System

```text
 fixed overhead camera
         │ frames
         ▼
 ┌──────────────────────────── laptop ─────────────────────────────┐
 │                                                                 │
 │  vision (#14)              bridge / app state (#16)             │
 │  predict(frame) ─────────► capture loop, bin decision gate, ────┼──► dashboard (#15, web/)
 │  label, confidence, bin    counts, /api/* server                │    GET /api/state
 │                                 │  arm.home() / arm.sort(bin)   │    GET /api/frame.jpg
 │                                 │  arm.stop()                   │    POST /api/stop
 │                                 ▼                               │
 │                            arm/ ArmController (#20)             │
 │                            states READY BUSY DONE ERROR         │
 └─────────────────────────────────┬───────────────────────────────┘
                                   │ USB (LeRobot, Feetech protocol)
                                   ▼
                   Hiwonder BusLinker V3 board ── 12 V servo supply
                                   │ daisy-chained 3-wire bus
                                   ▼
        SO-101 follower arm: 6 × Waveshare ST3215-HS (IDs 1–6) ──► item into bin

 ESP32-S3 (#13, PROPOSED, pending agreement on #1): physical STOP button + status LED,
 sends "STOP\n" to the laptop over USB serial. It does not drive the servos.
```

## Owners and folders

| Area | Folder | Owner | Issue |
| --- | --- | --- | --- |
| Arm hardware: SO-101 print, assembly, pickup and bin layout | (photos, notes, poses) | @PaulPio | #12 |
| Arm control on the laptop | `arm/` | @PaulPio | #20 |
| Servo power, wiring, physical stop, ESP32-S3 extras | TBD | @yusufdirdis | #13 |
| Camera and YOLO `predict(frame)` | TBD | @abhiwasabi | #14 |
| Dashboard | `web/` | @abhiwasabi | #15 |
| Bridge, app state, `/api/*` server | TBD | @anaoliveira01 | #16 |
| End-to-end trials, submission | — | @anaoliveira01 | #17 |

## Contracts

Change these only after posting on the Message Board and getting agreement from the people they affect.

### Vision → bridge (#14 → #16)

The vision step returns `label` (string), `confidence` (0–1, may be absent), and `bin`: one of `"PAPER"`, `"PLASTIC"`, `"METAL"`, `"ORGANIC"` (issue #23), or `None`. With no detection or low confidence it returns `label="UNKNOWN"` and `bin=None`. The model (YOLO or Gemini) is still being settled in #14/#16. Demo items, one or more per bin, are not chosen or measured yet.

### Bridge → arm (#16 → #20)

Use either form. Both take the same commands and report the same states; only the in-process form also exposes `connected` and `last_done` as values.

**In-process (recommended):**

```python
from arm import ArmConfig, ArmController

config = ArmConfig.from_env()                    # ARM_PORT=COM5, etc.
arm = ArmController.from_config(config)          # mock=True for no hardware; checks settings and
                                                 # poses before opening the port
arm.on_state(lambda state, detail: ...)          # state is ArmState.READY/BUSY/DONE/ERROR
arm.home()               # -> bool accepted
arm.sort("PAPER")        # PAPER/PLASTIC/METAL/ORGANIC (arm.BINS) -> bool accepted; arm.last_rejection says why not
arm.stop()               # always accepted
arm.state, arm.detail    # current state and reason; str(arm.state) == "READY" etc. (use it for arm_state)
arm.poll()               # call ~1/s: checks the arm answers; returns arm.connected; never raises
arm.last_done            # (sequence number, "SORT PAPER") of the last completed command
arm.close()              # on shutdown: stops motion, refuses new commands, then disconnects. Returns
                         # True when disconnected; if the servo bus is hung it returns False and the
                         # stuck operation disconnects as soon as it ends (never concurrently)
```

**Behavior the bridge can rely on:**

| Question | Answer |
| --- | --- |
| Do `home()` / `sort()` block? | No. They return at once: `True` means accepted and the arm is already `BUSY`; `False` means rejected and nothing moved. `arm.last_rejection` gives the reason for the calling thread's own last command. Motion runs on the controller's worker thread. If STOP had to give up on a hung servo bus ("stopped; arm not responding"), new commands are refused until that stopped motion ends; it never resumes. |
| How does STOP interrupt a move? | `stop()` can be called from any thread at any time, including mid-sort and from a listener. The move checks for it every control step (20 ms) and **freezes**: body joints hold where they are, and the gripper keeps its last command, so a carried item is not dropped. When `stop()` returns, `arm.state` is `ERROR` (detail `"stopped"`), typically within about 20 ms; the `ERROR` callback follows. This holds even if STOP races a new command or one that is just finishing. The rest of the sequence is cancelled; a stopped SORT never reports `DONE`. |
| How does DONE become READY? | When the last step finishes, the controller reports `DONE` (detail = the command) and then immediately `READY`. DONE is momentary, so do not wait to poll it. |
| How is connection loss shown? | `arm.connected` becomes `False` and the arm enters `ERROR` with detail `"connection lost: …"`. Only USB, power, or bus failures count; other faults (e.g. a servo refusing a command) report `"<command> failed: …"` or, from `poll()`, `"arm check failed: …"`, with `connected` unchanged. A move detects connection loss itself. While idle, `arm.poll()` detects it; `poll()` never touches the bus during a move, and a poll result overtaken by a newer command is discarded. After a power cut the next move starts from where the arm really is, not from its old goal. If the arm answers again, `poll()` sets `connected` back to `True`, but the state stays `ERROR` until HOME. After a USB unplug, restart the bridge so LeRobot reopens the port. Map `connected` to `/api/state.connected`. |
| How do I count each completed command exactly once? | `on_state` is called with `(ArmState.DONE, "SORT PAPER")` exactly once per completed command; count the bin there. If you poll instead, count when `arm.last_done[0]` increases. |
| Which thread calls `on_state` listeners? | One notifier thread owned by the controller (`arm-notifier`), always in state order. Never the worker or the `poll()` caller. Listeners may call `home()`, `sort()`, `stop()` and `poll()` (e.g. start the next sort on `READY`), but not `wait_idle()`. Keep them short: update the bridge state under its own lock and return. |

**Subprocess:** run `python -m arm.cli` (or `--mock`). Write `HOME`, `SORT <BIN>` (e.g. `SORT PAPER`), or `STOP` lines to its stdin. It prints exactly one state word per line on stdout and writes notes, every line starting with `#`, to stderr (rejection reasons, error details, warnings). While idle it checks the arm once a second, so a lost connection prints `ERROR` with a `# connection lost: …` note.

**Arm state machine:**

```text
 start ──► ERROR ("not homed") ──HOME──► BUSY ──► DONE ──► READY
 READY ──SORT <bin>──► BUSY ──► DONE ──► READY
 BUSY  ──STOP──► ERROR ("stopped", arm frozen, gripper keeps its grip) ──HOME──► BUSY ──► … READY
 BUSY  ──motion fault──► ERROR (arm frozen) ──HOME──► …
 any   ──connection lost──► ERROR, connected=False ──(arm answers again) poll()──► connected=True ──HOME──► …
```

- SORT is accepted only in READY and only for a known bin. HOME is accepted unless the arm is BUSY. A rejected command never moves the arm.
- The `DONE` callback's `detail` is the finished command (e.g. `"SORT PAPER"`). Count an item only on that DONE.
- Unknown or low-confidence predictions must never call `sort()`. The bridge decides; the arm only checks the bin name.
- With `MockBackend` (or `--mock`), the bridge works with no hardware.

### Bridge → dashboard (#16 → #15)

The bridge serves `web/` and this API on `127.0.0.1` only. POST requests must send a JSON object body; the Host and Origin headers are checked.

| Endpoint | Body | Response | Moves the arm? |
| --- | --- | --- | --- |
| `GET /api/state` | — | The current state (fields below) | No |
| `GET /api/frame.jpg` | — | Latest camera frame, or 503 | No |
| `GET /api/cameras` | — | `{devices: [{id, name, index}]}` | No |
| `GET /api/gemini` | — | `{configured, model}` | No |
| `POST /api/stop` | `{}` | `{accepted: true, state}`; always accepted | Stops it: `arm.stop()` |
| `POST /api/home` | `{}` | 200 `{accepted, state}`, or 409 `{accepted: false, error, state}` with the arm's reason | Yes: `arm.home()`; pauses auto-sorting |
| `POST /api/sort` | `{"bin": "PAPER"}` | 200; 409 with `error` (the arm's reason, or "the previous sort is still finishing"); or 400 for an unknown bin or while auto-sorting is on | Yes: a manual `arm.sort(bin)`. Needs no camera or vision; counted on DONE as `"Manual"` |
| `POST /api/sorting` | `{"enabled": true\|false}` | 200, or 400 with the reason start was refused | Indirectly: while enabled, each confident vision result triggers `arm.sort(bin)` |
| `POST /api/camera` | `{source: "mock"\|"usb"\|"esp32"\|"off", url?, index?, device_id?}` | 200 or 400 | No (pauses auto-sorting) |
| `POST /api/mock` | `{scenario}` | 200 or 400 (simulation only) | No |
| `POST /api/gemini` | `{key, model?}` or `{disconnect: true}` | `{configured, model}` | No (pauses auto-sorting) |

`GET /api/state` returns `label`, `confidence`, `bin`, `arm_state` (READY/BUSY/DONE/ERROR), `arm_detail` (the error text, or the running command while BUSY), `connected`, `bins` (the allowed bins, in order), and `counts` with exactly the keys `PAPER`, `PLASTIC`, `METAL`, `ORGANIC`. It also carries `recent_sorts` (completed sorts, added from the DONE callback), `sorting_enabled`, `status`, the camera and vision fields, an optional `prediction_at` (ISO 8601), and `public` / `operator` (see Online access). Details and examples: `web/README.md` and `bridge/README.md`.

### Online access (#36)

The site goes online through **Tailscale Funnel** while the arm stays on the laptop. Full steps: [docs/RUN_ONLINE.md](docs/RUN_ONLINE.md).

- **Two ports** on `127.0.0.1` share one bridge and arm:
  - the **local port** (4173): unchanged, no password, never exposed;
  - the **public port** (`--public-port`, e.g. 4174): the only one given to Funnel, and it always enforces the operator session.
  - Funnel traffic arrives from the laptop itself, so address or Host can't separate remote viewers from the local operator.
- **On the public port, without logging in:** the dashboard, `GET /api/state`, `GET /api/frame.jpg`, `GET /api/gemini`, and **`POST /api/stop`**. Anyone watching can stop the arm.
- **Operator only** (`POST /api/login` with the password from env `BRIDGE_OPERATOR_PASSWORD`): home, sort, sorting, camera, mock, Gemini changes, and `GET /api/cameras`. Without a session these return 401.
  - The session is an HttpOnly, Secure, SameSite=Strict cookie. Logout from one browser ends every operator session.
  - Failed logins are remembered until a correct password. The wait starts at 60 seconds and doubles, up to 15 minutes. The local port has no lockout.
  - Host and Origin are checked against `--public-host`.
  - Stopping the bridge closes both ports without waiting for a stuck request.
- **`/api/state.public`** is true on the public port. **`/api/state.operator`** is true on the local port, or with a valid session. The dashboard disables every control except STOP for viewers.

## Hardware

- **Arm:** [SO-101 follower](https://github.com/TheRobotStudio/SO-ARM100), printed in PLA+. Joints and Feetech IDs: 1 `shoulder_pan`, 2 `shoulder_lift`, 3 `elbow_flex`, 4 `wrist_flex`, 5 `wrist_roll`, 6 `gripper`.
- **Servos:** 6 × Waveshare **ST3215-HS** (20 kg·cm at 12 V, 106 rpm, 4096-step encoder, factory ID 1, 1 Mbps). This is the same size and protocol family as the STS3215 in the stock SO-101 parts list. They need a **12 V** supply: 5 A minimum, more is safer (2.4 A locked-rotor each). Each servo's ID must be set **before assembly** with `lerobot-setup-motors`. **Unverified:** whether LeRobot accepts their model number (it expects the STS3215's 777); the first one-servo test settles it.
- **Controller:** **Hiwonder BusLinker V3** → laptop over USB (CH341 driver, default 1 Mbps, 5–14 V input). Communication jumper on the **Servo + USB** pins. Hiwonder lists it as LeRobot-compatible, but does not name Feetech/ST3215. **Unverified:** that it passes Feetech packets straight through, and that its 3-pin port order matches the servo cable. Fallback: a Waveshare bus-servo adapter, the board in the stock SO-101 parts list.
- **Power cut:** the physical switch goes on the 12 V line into the BusLinker, so it cuts every servo at once.
- **Software:** Python 3.12, [LeRobot](https://github.com/huggingface/lerobot) `0.7.0` (`lerobot[feetech]`), which handles the bus, calibration, PID settings, and the gripper torque limit (50%).

## Safety rules (all code)

1. A rejected or unknown command never moves the arm.
2. STOP is always accepted. It cancels motion within one control step (20 ms) and **freezes** the arm: body joints hold where they are and the gripper keeps its last command, so neither the arm nor a carried item drops. Only HOME leaves ERROR.
3. Moves are slow by default (20 °/s) and interpolated. LeRobot also caps any single body-joint step at 10 °.
4. Any motion failure freezes the arm the same way and reports ERROR. Nothing retries automatically.
5. Settings are checked at start-up: a zero, negative, non-numeric, or too-high speed (ceiling 90 °/s, set in `arm/config.py`, not the environment) is refused instead of disabling the slow moves. The 10° cap never turns into a fast move: a goal still beyond it after a move is not re-sent; the move fails and holds. Connecting sets every goal to the arm's present position before torque comes on, so the arm cannot jump to an old goal.
6. During testing, keep a hand on the servo power switch. Exiting the arm programs turns torque off, so the arm goes limp: end at rest or support it.

## Setup order

1. Set servo IDs (before assembly) → 2. assemble → 3. `lerobot-calibrate` → 4. teach poses (`python -m arm.teach`) → 5. bench test (`python -m arm.cli`) → 6. connect the bridge → 7. connect vision. Details are in [arm/README.md](arm/README.md).
