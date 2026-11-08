# Laptop integration bridge (#16)

Serves the existing dashboard and `/api/*` on one local origin. Camera capture, classification, and arm polling run on separate workers so a cloud request never owns the STOP path. With a simulated arm, sorting arms automatically once the operator homes the arm and vision is ready. A physical arm still starts paused and requires an explicit Start sorting command.

## Arm package

The merged `arm/` package provides the controller and its mock. Run from the repository root. Python 3.12 is the team's target for LeRobot; the mock controller requires Python 3.11 or newer. Full mock mode needs neither a Gemini key nor camera access unless you select a real camera in Settings.

For a laptop or iPhone camera, create one virtual environment and install the live-camera dependencies into it before starting the bridge:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r bridge/requirements.txt
.venv/bin/python -m bridge --mock
```

On macOS, the requirements include PyObjC so the camera list can find Continuity Camera. Open **Settings**, choose **iPhone camera**, click **Refresh cameras** if needed, then **Use this camera**. If the phone is absent, connect it to the Mac, accept **Trust**, enable Continuity Camera, lock the phone, and refresh. The phone must also be available to macOS when the bridge opens it; another app using the camera can prevent capture. Use the same `.venv/bin/python` for installation and every bridge launch.

## Full simulation

```sh
.venv/bin/python -m bridge --mock
```

Open http://127.0.0.1:4173 (without `?demo=1`, which is the dashboard's separate static preview).

1. In Overview or Arm status, click **Home arm** and wait for READY.
2. In Settings, choose **Plastic bottle** under Simulated pickup area. Sorting starts automatically after HOME.
4. Observe BUSY, then READY, and one plastic count. Leaving the bottle selected must not trigger another sort.
5. Choose **Paper sheet** directly for a paper cycle. A different supported item can sort without an empty-area step. The same item is not sorted again until two empty samples are seen.
6. STOP pauses sorting and leaves ERROR. Home the simulated arm to recover and resume automatic sorting.

The header always labels the simulated arm. Sample images are illustrations; the mock frame endpoint returns SVG with its correct media type. Live mode returns JPEG. Counts mean controller-completed sequences; they do not prove a successful physical grip or drop. Counts reset on process restart.

## Gemini and a real camera, simulated arm

```sh
.venv/bin/python -m pip install -r bridge/requirements.txt
export GEMINI_API_KEY='your-key'
export GEMINI_MODEL='your-supported-image-model-id'
.venv/bin/python -m bridge --mock-arm --camera 0
```

Set keys in your shell environment; never put them in `web/`, a URL, or Git. This mode sends camera images to the configured Gemini API when sorting is enabled. The model ID is explicit rather than hardcoded. The request uses Gemini's `generateContent` REST API with image input and a structured schema.

## Optional Auth0 operator login

The dashboard can use Auth0 Universal Login for operator controls. Without Auth0 variables, the local demo works exactly as before. To enable login:

1. In Auth0, create a **Single Page Application** and a separate **API**. Give the API an identifier such as `https://sharkbyte.local/api`; this is the audience, not a server URL.
2. Create the API permission `sorter:operate`. Enable RBAC and **Add Permissions in the Access Token** for that API. Assign the permission to the operator through a role. A signed-in user without this permission cannot send commands.
3. For the SPA, set **Allowed Callback URLs**, **Allowed Logout URLs**, and **Allowed Web Origins** to `http://127.0.0.1:4173/` (adjust the port if needed). Use the same hostname in the browser.
4. Install the optional verifier dependency and start the bridge with the three non-secret identifiers:

```sh
.venv/bin/python -m pip install -r bridge/auth0-requirements.txt
export AUTH0_DOMAIN='your-tenant.us.auth0.com'
export AUTH0_CLIENT_ID='your-spa-client-id'
export AUTH0_AUDIENCE='https://sharkbyte.local/api'
.venv/bin/python -m bridge --mock
```

`GET /api/auth-config` exposes only the public configuration needed by the browser. The dashboard loads Auth0's pinned SPA SDK only in configured mode, uses its Universal Login, and attaches an API access token to control requests. The Python bridge validates the token's signature, issuer, audience, expiry, and `sorter:operate` permission. HOME, sorting, camera selection, mock scenarios, and Gemini settings require the permission. **STOP remains available without login** so an operator can stop the arm immediately. State and camera-frame reads remain available on the local dashboard. Partial Auth0 configuration fails at startup rather than silently leaving controls open. No client secret is needed or stored.

The Auth0 tenant has not yet been created for this project; the configured path is covered by mocked tests but still needs a live login test with the team's tenant before it can be claimed as a working sponsor integration.

### ESP32-S3 camera source

The XIAO ESP32-S3 camera must first serve JPEG snapshots over local Wi-Fi. Follow [the XIAO camera setup guide](../docs/xiao-esp32s3-camera.md) to get a URL such as `http://192.168.1.50/capture`; USB serial alone does not expose a camera feed to this bridge.

In dashboard **Settings**, choose **ESP32-S3 camera**, enter that snapshot URL, and click **Use this camera**. The selection changes at runtime. `GET /api/state` reports `camera_source`, `camera_url`, `camera_error`, `vision_ready`, and `mock_available`. `POST /api/camera` accepts `{"source":"esp32","url":"http://192.168.1.50/capture"}`; `{"source":"usb","index":0}` selects a laptop/USB camera, and `{"source":"mock"}` is available when the bridge was started with `--mock`. Switching sources clears the previous frame and prediction. Simulated-arm sorting resumes automatically when the new source and vision are ready.

To preview ESP32 frames before Gemini is configured, start `python3 -m bridge --mock`, then select the ESP32 source in Settings. The arm stays simulated. Automatic sorting waits until Gemini is connected and the arm is homed. You can choose an initial ESP32 source at launch with `--esp32-url http://192.168.1.50/capture`.

The ESP32 URL must use plain HTTP with a private or loopback IP address and return one JPEG per request. Redirects, credentials in the URL, invalid images, and oversized responses are rejected. Keep the dashboard bound to `127.0.0.1`.

Demo policy: clearly visible plastic bottle → PLASTIC; paper sheet → PAPER; metal can → METAL; banana peel → ORGANIC; other items, ambiguity, multiple items, or obscured views → review/no movement. This is a selected-object demonstration, not a general recycling classifier. Coordinate policy/predictor changes with Abhi (#14). Gemini does not provide a calibrated detector probability here, so confidence remains null and the dashboard shows an em dash while displaying the validated bin.

The adapter boundary is `predict(frame) -> {label, item_present, needs_review, bin, confidence?}`. `frame` contains bytes and MIME type. The bridge timestamps the source frame and discards stale or invalidated results. In simulated-arm mode, a new supported label/bin can sort as soon as the arm is ready, even if the prior item was not cleared. An unchanged label/bin is suppressed until two distinct empty frames are classified. Physical-arm mode retains the empty-area latch. Simulated-arm mode retries temporary Gemini 5xx and incomplete-response errors with increasing delay; authentication, quota, and other cloud failures pause sorting. STOP remains latched until HOME. False class changes can still trigger an unwanted simulated sort, so physical behavior remains gated pending trials.

## Physical arm (bench validation required)

```sh
.venv/bin/python -m pip install -r arm/requirements.txt
export ARM_PORT='/your/verified/usb-port'
.venv/bin/python -m bridge --camera 0
```

Also configure Gemini as above. Paul must first verify the ST3215-HS motor model and Hiwonder BusLinker V3 transport, calibrate, teach real poses, and test clearance. The reported hardware is approximately 20 kg·cm ST3215-HS on 12 V; the current LeRobot adapter is not yet proven with it. This bridge does not raise real motion speeds or substitute mock poses on physical hardware. STOP uses the local controller, not Gemini. Closing the controller can release torque; support the arm at shutdown and keep the physical power cutoff accessible.

## Optional Tiger Data sort log (#47)

Off unless `TIGER_DATABASE_URL` is set. When set, the bridge records each sort's outcome in a `sort_events` table (a TimescaleDB hypertable on Tiger Cloud):

```sh
.venv/bin/python -m pip install -r bridge/requirements.txt   # includes psycopg
export TIGER_DATABASE_URL='postgres://...'                   # your Tiger Cloud service URL; never commit it
.venv/bin/python -m bridge --mock
```

- One row per accepted sort: `DONE` after the controller completes it, `STOPPED` after an operator STOP, or `FAULT` for any other arm error during the sort, with `label`, `bin`, nullable `confidence`, `cycle_s`, `simulated`, and the error `detail`. HOME and idle errors write nothing. Counts and history still update only on DONE.
- The bridge only enqueues (500 events, dropped when full). A background thread writes in batches and retries with backoff, so a slow or unreachable database never delays HOME, SORT, or STOP.
- Rows from `--mock`/`--mock-arm` have `simulated = true`; they are not physical drops.
- The writer also creates `sort_events_per_minute`, a real-time TimescaleDB continuous aggregate (per-minute counts and cycle times by bin and outcome) with a refresh policy.
- `GET /api/sort-history?minutes=60` (read-only, 5–1440 minutes) returns outcome totals, a per-minute series and per-bin counts for the window, and the latest rows. It reads the continuous aggregate, falling back to the table, on its own short-lived connection with a 5 s statement timeout and a 3 s cache. Without the URL it returns `{"enabled": false}`; database errors return a generic `ok: false`. The dashboard's **Sort history · Tiger Data** card under Bin totals shows it.

Real-arm startup needs all of the following:

- Taught `home`, `above_pick`, `pick`, and `above_paper`, `above_plastic`, `above_metal`, `above_organic` poses, plus open/closed gripper settings. Teach them with `python -m arm.teach`.
- A matching LeRobot calibration file for the configured arm ID; run `lerobot-calibrate` first.
- `ARM_PORT` set to the verified serial port.
- Both `GEMINI_API_KEY` and `GEMINI_MODEL` in the environment.
- An available laptop/USB camera (`--camera`) or a working local ESP32 snapshot source (`--esp32-url`).

Arm setup errors print a next step and exit with status 2. If the dashboard port is occupied, choose another with `--port`; the bridge stops and closes the arm and camera on server startup failure.

## API

| Route | Behavior |
| --- | --- |
| `GET /api/state` | Prediction, controller, counts, camera source/status, simulation, sorting state, and ordered allowed `bins` |
| `GET /api/sort-history` | Optional Tiger Data summary (`?minutes=5..1440`); `{"enabled": false}` without `TIGER_DATABASE_URL` |
| `GET /api/frame.jpg` | Latest fresh frame, or 503; live JPEG / mock SVG |
| `POST /api/stop` | Pause, invalidate pending results, call `arm.stop()` |
| `POST /api/home` | Pause, invalidate pending results, request operator HOME; 409 if rejected |
| `POST /api/sort` | JSON `{"bin":"PAPER"}`; manually sort to an allowed bin while automatic sorting is paused; no camera or vision required |
| `POST /api/sorting` | JSON `{"enabled": true}` or false; start requires READY/connected/fresh camera |
| `POST /api/mock` | Full simulation only: `{"scenario":"empty"}`; bottle, paper, metal, organic, unknown, camera-offline also supported |
| `POST /api/camera` | Select `mock`, `usb`, or `esp32` camera source; pauses sorting and clears the old decision |

The server binds only to 127.0.0.1, serves static files only from `web/`, and rejects foreign hosts/origins for commands. The default port is for local operation; use the separate authenticated public port below for remote access. Pause allows a current move to finish; STOP interrupts it. HOME completion never increments bin counts. Completion callbacks run on the controller notifier thread and update bridge state briefly under a lock; STOP waits outside that lock.

`POST /api/sort` returns 200 with `{"accepted":true,"state":...}` when accepted. Rejected manual SORT and HOME requests return 409 with `{"accepted":false,"error":"<reason>","state":...}`: the arm's reason, or, for SORT, "The previous sort is still finishing; try again in a moment." while an earlier sort has not reported DONE yet (the arm shows READY a moment before its DONE reaches the bridge). A missing, non-string, or unsupported bin returns 400 with `{"error":...}`. Manual sorting while automatic sorting is enabled returns 400 with `{"error":"Pause automatic sorting before a manual sort."}`. Bin names must exactly match the uppercase values in `/api/state.bins` (normally PAPER, PLASTIC, METAL, ORGANIC). A manual sort increments its bin count and adds a `Manual` recent-sort record only after the controller reports matching DONE. Automatic sorting remains paused; the pickup area must clear before another automatic sort.

## Online access (Tailscale Funnel)

Run two loopback servers sharing the same arm, camera, and bridge state. The local
dashboard (`--port`, default **4173**) keeps password-free operator control. The
separate public server (`--public-port`, for example **4174**) requires an operator
session for controls, even when a request arrives from localhost. **Never give
port 4173 (or your chosen local `--port`) to Funnel. Forward only the public port.**
Funnel forwards HTTPS at `https://<laptop>.<tailnet>.ts.net` to that loopback port;
request IPs cannot distinguish remote visitors from the local operator.

Set `BRIDGE_OPERATOR_PASSWORD` in the launch shell environment to a private password
of at least eight characters. It is accepted only through that environment variable,
never a command-line flag, and the bridge never logs it or writes it to a file.
Then launch, for example:

```sh
python -m bridge --mock --public-port 4174 --public-host sorter.tail1234.ts.net
```

Use your actual Funnel hostname for `--public-host`; it is required whenever
`--public-port` is set. Missing or short passwords exit with status 2 before opening
the camera or arm. Startup prints both URLs without printing the password.

Public visitors can load the dashboard, read `GET /api/state`, `GET /api/frame.jpg`, `GET /api/gemini`, `GET /api/sort-history`, `GET /api/auth-config` and `GET /api/operator`, and use **STOP** without logging in. STOP is deliberately
open to everyone. HOME, manual sort, start/pause sorting, camera selection, mock
selection, Gemini changes, and `GET /api/cameras` require login.

One `Handler.access()` check decides operator access for protected POSTs and GETs.
A verified Auth0 token with `sorter:operate` is allowed; a token without the
permission returns 403 `Operator permission required`; otherwise a public-port
password session is allowed; a local port with no Auth0 verifier stays open; every
other request returns 401 `Operator login required`.

`POST /api/login` accepts JSON `{"password":"..."}` and sets an HttpOnly, Secure,
SameSite=Strict `sorter_session` cookie for 12 hours. Use the public HTTPS URL for
browser login; the public port's HTTP URL is the local Funnel forwarding target.
Failed attempts are kept until a correct password. The fifth failure blocks the
next login for 60 seconds, and each later failure doubles that wait, up to 15
minutes. A blocked login returns `429` with `Too many attempts. Try again in N
seconds.` The laptop port (`--port`, default 4173) has no password and no lockout.
`POST /api/logout` with a live session ends every operator session and expires
the caller's cookie. A logout without a live session does not end other sessions.
Sessions stay in memory and disappear on restart. State responses expose `public`
and `operator` so the dashboard can show the current access level. The local port
always reports `public: false` and `operator: true`. Requests time out after 30
seconds, and stopping the bridge closes the listening sockets without waiting for
a stuck request.

## Checks

```sh
python3 -m unittest discover -s bridge/tests -v
node --test web/*.test.mjs
node --check web/app.mjs
```

`bridge/tests/test_arm_integration.py` also exercises Paul's actual controller against MockBackend when `arm/` is available. Gemini requests are mocked in tests. Real cloud, camera, and motor behavior still require integration trials (#17).

## Four-bin dependency update

The dashboard follows PR #24: PAPER, PLASTIC, METAL, ORGANIC. The runtime reads allowed routes from `arm.BINS` and blocks Start sorting if the controller exposes the old two-bin contract. Startup uses ArmController.from_config(config, mock=...). Tests exercise the four-bin controller and completion callbacks. `recent_sorts` contains at most 20 completed SORT records with stable UUIDs, captured labels/bins, and completion timestamps. STOP/failure drops the pending record; HOME is never counted.

## Current verification and next work

User-tested real camera + Gemini (`gemini-3.1-flash-lite`) with a simulated arm: plastic and metal worked. Paper testing hit Gemini quota; organic and physical hardware remain unverified. Camera images are resized to at most 960 pixels on the longest side and JPEG quality 80; Gemini requests time out after 30 seconds. Safe API diagnostics distinguish quota, authentication, transport, and malformed-response failures without exposing credentials.

Sorting currently makes repeated classification requests, including empty-area checks. Pause sorting when finished to conserve quota. A Classify once control is planned but is not implemented in this change. YOLO is not connected yet; the direct-Gemini adapter is a test path pending Abhi's vision integration. All API keys remain environment variables.


### Camera and Gemini controls

Install `bridge/requirements.txt` in the same virtual environment used to launch the server.
Camera Settings are available in both Settings and the Camera tab. Named webcam selection
uses stable device IDs so choosing the MacBook camera does not fall back to an iPhone.
Stop camera releases the device, clears the frame, and pauses sorting. Start camera uses
the selected source. ESP32 CameraWebServer inputs try the continuous MJPEG stream on
port 81 (or snapshot port + 1), with snapshot fallback for servers without streaming.
A stream improves refresh frequency but does not guarantee low Wi-Fi latency.

Settings accepts a Gemini key for this server session only. The key is never returned by
an API, stored in browser storage, or written to a file. Connect lists available text/image
Flash models and probes up to three candidates before accepting one, preferring Flash-Lite.
A successful probe checks generation access; image classification can still fail because
of quota, service availability, or image/schema compatibility. Camera images are sent to
Google only when recognition is running. Disconnect drops the session predictor and pauses
sorting; restarting the server also requires reconnecting. Both actions invalidate pending
classification results. Environment-based `GEMINI_API_KEY` and `GEMINI_MODEL` still work.

Additional endpoints:
- `GET /api/cameras`: connected webcam names and device IDs.
- `POST /api/camera` with `source: "usb"` and `device_id`: select a named webcam.
- `POST /api/camera` with `source: "off"`: release the camera and pause sorting.
- `GET /api/gemini`: configured flag and model ID, never the key.
- `POST /api/gemini` with `key` (and optional model ID): validate and connect.
- `POST /api/gemini` with `disconnect: true`: remove the session connection.

The dashboard labels a pending classification separately from an unknown item. After a
confirmed sort it shows the latest classified item, while an unchanged item is not sorted again.
The UI displays EMPTY as Unknown item; the backend uses two empty results to allow a
repeat of the same item. Simulated arm mode remains explicitly labeled and does not
operate physical hardware.

Camera selection uses one dropdown: Laptop webcam, ESP32, or iPhone camera. Laptop and
iPhone choices resolve automatically to distinct device IDs; missing devices disable
connection rather than falling back to a different camera. Refresh camera list retries
discovery. On macOS, discovery opts the Python process into Continuity Camera without
editing the global Python Info.plist. Connect/trust the iPhone and enable Continuity
Camera before refreshing. `/api/state.camera_device_id` identifies the active named
webcam so reloading the UI can restore the correct camera category.
