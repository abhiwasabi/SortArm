# Laptop integration bridge (#16)

Serves the existing dashboard and `/api/*` on one local origin. Camera capture, classification, and arm polling run on separate workers so a cloud request never owns the STOP path. Sorting starts paused and the arm must be homed by an operator.

## Dependency on Paul's arm code

`arm/` is owned by Paul in #20. This branch does not copy or modify it. Until #20 lands, fetch his branch and expose its package from a temporary directory without merging branches:

```sh
git fetch origin
mkdir -p /tmp/sharkbyte-arm-reference
git archive origin/20-so101-arm-control arm | tar -x -C /tmp/sharkbyte-arm-reference
export PYTHONPATH=/tmp/sharkbyte-arm-reference
```

After #20 lands, run from the repository root normally. Python 3.12 is the team's target for LeRobot; the current mock controller requires Python 3.11 or newer. The bridge's mock mode uses only Python's standard library and the `arm/` mock, with no Gemini key, camera access, or LeRobot install.

## Full simulation

```sh
.venv/bin/python -m bridge --mock
```

Open http://127.0.0.1:4173 (without `?demo=1`, which is the dashboard's separate static preview).

1. In Overview or Arm status, click **Home arm** and wait for READY.
2. Click **Start sorting**.
3. In Settings, choose **Plastic bottle** under Simulated pickup area.
4. Observe BUSY, then READY, and one plastic count. Leaving the bottle selected must not trigger another sort.
5. Choose **Empty**, wait at least four seconds (two classification samples), then choose **Paper sheet** for a paper cycle.
6. STOP pauses sorting and leaves ERROR. Home the arm and explicitly start sorting to recover.

The header always labels the simulated arm. Sample images are illustrations; the mock frame endpoint returns SVG with its correct media type. Live mode returns JPEG. Counts mean controller-completed sequences; they do not prove a successful physical grip or drop. Counts reset on process restart.

## Gemini and a real camera, simulated arm

```sh
python3 -m pip install -r bridge/requirements.txt
export GEMINI_API_KEY='your-key'
export GEMINI_MODEL='your-supported-image-model-id'
.venv/bin/python -m bridge --mock-arm --camera 0
```

Set keys in your shell environment; never put them in `web/`, a URL, or Git. This mode sends camera images to the configured Gemini API when sorting is enabled. The model ID is explicit rather than hardcoded. The request uses Gemini's `generateContent` REST API with image input and a structured schema.

Demo policy: clearly visible plastic bottle → PLASTIC; paper sheet → PAPER; metal can → METAL; banana peel → ORGANIC; other items, ambiguity, multiple items, or obscured views → review/no movement. This is a selected-object demonstration, not a general recycling classifier. Coordinate policy/predictor changes with Abhi (#14). Gemini does not provide a calibrated detector probability here, so confidence remains null and the dashboard shows an em dash while displaying the validated bin.

The adapter boundary is `predict(frame) -> {label, item_present, needs_review, bin, confidence?}`. `frame` contains bytes and MIME type. The bridge timestamps the source frame, discards results older than eight seconds for hardware or 30 seconds for a simulated arm or invalidated by STOP/pause/camera loss, checks READY/connected, and latches each accepted item until two distinct empty frames have been classified. Cloud failures pause sorting; no automatic cloud retry or motion retry occurs. False empty-area classifications can still rearm the system: evaluate that behavior during trials.

## Physical arm (bench validation required)

```sh
python3 -m pip install -r arm/requirements.txt
export ARM_PORT='/your/verified/usb-port'
.venv/bin/python -m bridge --camera 0
```

Also configure Gemini as above. Paul must first verify the ST3215-HS motor model and Hiwonder BusLinker V3 transport, calibrate, teach real poses, and test clearance. The reported hardware is approximately 20 kg·cm ST3215-HS on 12 V; the current LeRobot adapter is not yet proven with it. This bridge does not raise real motion speeds or substitute mock poses on physical hardware. STOP uses the local controller, not Gemini. Closing the controller can release torque; support the arm at shutdown and keep the physical power cutoff accessible.

## API

| Route | Behavior |
| --- | --- |
| `GET /api/state` | Existing prediction, connection, state, and counts; adds `simulation`, `mock_camera`, `sorting_enabled`, `status`, `arm_detail`, `awaiting_clear` |
| `GET /api/frame.jpg` | Latest fresh frame, or 503; live JPEG / mock SVG |
| `POST /api/stop` | Pause, invalidate pending results, call `arm.stop()` |
| `POST /api/home` | Pause, invalidate pending results, request operator HOME; 409 if rejected |
| `POST /api/sorting` | JSON `{"enabled": true}` or false; start requires READY/connected/fresh camera |
| `POST /api/mock` | Full simulation only: `{"scenario":"empty"}`; bottle, paper, metal, organic, unknown, camera-offline also supported |

The server binds only to 127.0.0.1, serves static files only from `web/`, and rejects foreign hosts/origins for commands. Keep it local; it is not an authenticated remote-control service. Pause allows a current move to finish; STOP interrupts it. HOME completion never increments bin counts. Completion callbacks run on the controller notifier thread and update bridge state briefly under a lock; STOP waits outside that lock.

## Checks

```sh
python3 -m unittest discover -s bridge/tests -v
node --test web/*.test.mjs
node --check web/app.mjs
```

`bridge/tests/test_arm_integration.py` also exercises Paul's actual controller against MockBackend when `arm/` is available. Gemini requests are mocked in tests. Real cloud, camera, and motor behavior still require integration trials (#17).

## Four-bin dependency update

The dashboard now follows PR #24: PAPER, PLASTIC, METAL, ORGANIC. The runtime reads allowed routes from `arm.BINS` and blocks Start sorting if the controller still exposes the old two-bin contract. Use Paul's pushed four-bin commit a8a87de or newer. Startup uses ArmController.from_config(config, mock=...). Tests exercise the four-bin controller and completion callbacks. `recent_sorts` contains at most 20 completed SORT records with stable UUIDs, captured labels/bins, and completion timestamps. STOP/failure drops the pending record; HOME is never counted.

## Current verification and next work

User-tested real camera + Gemini (`gemini-3.1-flash-lite`) with a simulated arm: plastic and metal worked. Paper testing hit Gemini quota; organic and physical hardware remain unverified. Camera images are resized to at most 960 pixels on the longest side and JPEG quality 80; Gemini requests time out after 30 seconds. Safe API diagnostics distinguish quota, authentication, transport, and malformed-response failures without exposing credentials.

Sorting currently makes repeated classification requests, including empty-area checks. Pause sorting when finished to conserve quota. A Classify once control is planned but is not implemented in this change. YOLO is not connected yet; the direct-Gemini adapter is a test path pending Abhi's vision integration. All API keys remain environment variables.
