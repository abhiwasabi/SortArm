# Sorting dashboard

This folder is the static dashboard for issue #15. It contains no backend or hardware control code.

## Preview with sample data

From the repository root, run:

```sh
python3 -m http.server 4173 --directory web
```

Open `http://localhost:4173/?demo=1`. This shows illustrative items, counts, and history for the four material bins so the layout can be reviewed before the backend is running. The line under **Live sorting** identifies the sample preview. The Stop button does not contact hardware or the API in this preview.

## Use with the laptop backend

### Public viewers and operators

The normal local port (default `4173`) keeps password-free control. The separate public port is intended for the Tailscale Funnel HTTPS URL: anyone may watch the camera, prediction, arm state and counts, and **STOP is available to everyone**. Only an authenticated operator may issue other commands or enumerate laptop cameras. See [bridge online setup](../bridge/README.md#online-access-tailscale-funnel) for the separate public listener and password configuration. Forward the public listener to Funnel, not the password-free local port.

`GET /api/state` includes `public` and `operator`. A public viewer receives `{public: true, operator: false}`; after login the public state reports `operator: true`. Local responses use `{public: false, operator: true}`. Missing fields default to local/operator behavior for older bridges.

On a public dashboard, the **Operator access** panel offers a password field and **Log in**, or displays **Logged in as operator** with **Log out**. Login sends `POST /api/login {password}` using same-origin credentials; the bridge sets an HttpOnly session cookie, which the browser handles. The dashboard clears the password field immediately after starting each attempt, clears it on failures too, and never writes the password to localStorage, sessionStorage, state snapshots, or a global variable. The password input supports the browser's `current-password` autocomplete. Wrong-password (`401`) and rate-limit (`429`) messages are displayed verbatim. Logout sends `POST /api/logout` and returns to viewer mode. On the bridge, that logout also ends any other operator session.

Viewers cannot use Home, Start/Pause sorting, manual sorting, camera selection or Start/Stop camera, Gemini connect/disconnect, or mock scenarios. Camera discovery waits until operator access is known and runs after login. Viewer mode itself does not produce an error. The panel is hidden on the local port and in `?demo=1`; the sample preview continues to make no API or hardware commands.

An expired session that causes a command or camera-discovery `401` displays **Operator login required**, immediately disables operator controls and refreshes state. Commands are not retried automatically. State and command responses that predate login/logout are ignored so they cannot restore stale permissions. The bridge enforces authorization; hiding controls in the browser is only the interface behavior.

### Endpoints

The laptop bridge serves `web/` at the same origin as these endpoints. POST bodies are JSON objects; use `{}` for commands with no parameters. Motion entries below describe hardware mode; the simulated bridge runs the same commands without moving a physical arm.

| Endpoint | Request | Response | Moves the arm? |
| --- | --- | --- | --- |
| `GET /api/state` | No body | Current state: `public`, `operator`, prediction, `bins`, `arm_state`, `arm_detail`, `connected`, `sorting_enabled`, camera/vision status, confirmed `counts`, and optional `recent_sorts`; see example below | No |
| `GET /api/frame.jpg` | No body; optional cache-busting query | Latest fresh JPEG frame, or the selected mock image with its image content type; `503 {error}` if unavailable | No |
| `GET /api/cameras` | No body | `{devices: [{id, name, index}]}` for connected camera devices; `503 {error}` if enumeration fails | No |
| `GET /api/gemini` | No body | `{configured: boolean, model: string or null}`; never returns the API key | No |
| `POST /api/stop` | `{}` | `200 {accepted: true, state}` or `409 {accepted: false, state}`. Request acceptance alone does not confirm a stop; the dashboard waits for a subsequent connected `ERROR` state | Stops motion; does not start a new move |
| `POST /api/home` | `{}` | `200 {accepted: true, state}`; rejection: `409 {accepted: false, error, state}` with the arm's reason | Yes: starts the Home routine; sorting stays paused |
| `POST /api/sort` | `{bin: "PAPER"}` using a bin in `state.bins` | `200 {accepted: true, state}`; arm rejection, or the previous sort still finishing: `409 {accepted: false, error, state}`; invalid bin or automatic sorting enabled: `400 {error}`. No camera or vision required | Yes: starts a manual pick-and-drop routine |
| `POST /api/sorting` | `{enabled: true}` or `{enabled: false}` | `200 {accepted: true, state}`; invalid or unavailable start: `400 {error}` | Enabling allows future automatic sorts; pausing does not itself issue a movement |
| `POST /api/camera` | `{source: "usb", index: 0, device_id: "optional device ID"}`, `{source: "esp32", url: "http://192.168.1.50/capture"}`, `{source: "mock"}`, or `{source: "off"}` | `200 {accepted: true, state}`; invalid/unavailable source: `400 {error}` | No direct move; pauses automatic sorting and clears prior camera/prediction data |
| `POST /api/mock` | `{scenario: "empty"}`; also `bottle`, `paper`, `metal`, `organic`, `unknown`, or `camera-offline` | `200 {accepted: true, state}`; invalid scenario or inactive mock camera: `400 {error}` | No direct move; a supported sample can trigger a sort if automatic sorting is enabled |
| `POST /api/gemini` | Connect: `{key: "API key", model: "optional model ID"}`; disconnect: `{disconnect: true}` | `{configured: boolean, model: string or null}`; validation/provider failure: `400 {error}`. Credentials stay in server process memory; connecting and disconnecting pause sorting | No |
| `POST /api/login` | `{password: "operator password"}` on the public listener | `200 {operator: true, state}` and an HttpOnly session cookie; `401 {error: "Wrong password."}` or `429 {error: "Too many attempts. Try again in 60 seconds."}` (the wait doubles on later failures, up to 15 minutes) | No |
| `POST /api/logout` | `{}` on the public listener | `200 {operator: false, state}` and an expired session cookie. A live session also ends every other operator session | No |

Control endpoints require the configured same-origin dashboard (`403 {error}` otherwise). On the public listener, Home, Sort, Sorting, Camera, Mock and Gemini POSTs plus `GET /api/cameras` also require an operator session (`401 {error: "Operator login required"}` otherwise). State, frames, Gemini's credential-free status and STOP remain public. Unexpected controller-operation failures return `503 {error}`. A successful Home or Sort POST means the command was accepted, not that the move completed. The dashboard displays the response body's `error` when Home, Sort, or Sorting is rejected.

Example state:

```json
{
  "public": false,
  "operator": true,
  "label": "plastic bottle",
  "confidence": 0.94,
  "bin": "PLASTIC",
  "arm_state": "READY",
  "connected": true,
  "bins": ["PAPER", "PLASTIC", "METAL", "ORGANIC"],
  "sorting_enabled": false,
  "arm_detail": "Ready",
  "counts": {"PAPER": 4, "PLASTIC": 3, "METAL": 1, "ORGANIC": 2},
  "prediction_at": "2026-11-07T05:30:00Z",
  "recent_sorts": [
    {"id": "sort-10", "label": "plastic bottle", "bin": "PLASTIC", "completed_at": "2026-11-07T05:29:52Z"},
    {"id": "sort-9", "label": "paper card", "bin": "PAPER", "completed_at": "2026-11-07T05:28:11Z"},
    {"id": "sort-8", "label": "metal can", "bin": "METAL", "completed_at": "2026-11-07T05:27:04Z"},
    {"id": "sort-7", "label": "dry leaf", "bin": "ORGANIC", "completed_at": "2026-11-07T05:26:22Z"}
  ]
}
```

The supported `bin` values and `counts` keys are `PAPER`, `PLASTIC`, `METAL`, and `ORGANIC`. Unknown items should use `label: "UNKNOWN"` and `bin: null`. A valid backend bin decision can be displayed with `confidence: null`; the dashboard shows no percentage in that case. The backend must make the uncertainty and safety decision before dispatching a SORT command. `prediction_at` is an optional ISO 8601 time for the latest *prediction*. If it is older than eight seconds, the dashboard hides that detection. Without it, the dashboard labels the value **Latest response**, never a live detection. It clears predictions when the API cannot be reached.

`recent_sorts` is an optional, bounded list of completed sorts, newest first. Each entry has a stable `id`, the accepted item's `label`, one of the four destination `bin` values, and an ISO 8601 `completed_at` time. The backend should capture the label and bin when it accepts a sort, then append one entry when that command reports DONE, alongside incrementing the matching count. Predictions, rejected commands, STOP, HOME, and failed moves must not create history entries. If the field is omitted, the dashboard shows history as unavailable.

If the camera endpoint fails or no new frame loads for several seconds, the dashboard hides the last prediction and route. A successful Stop request means only that the request reached the backend; the dashboard waits for a controller state update and warns if none arrives.

The dashboard never increments counts itself. The backend should update counts only after the arm controller reports DONE. The physical arm also needs four taught drop positions, `above_paper`, `above_plastic`, `above_metal`, and `above_organic`, before it can route to all four bins. For live use, open the backend's dashboard URL without `?demo=1`; serving this folder on a separate port will show the API as offline unless the backend also serves the API on that origin. The Settings view shows the API and arm connection status, while the camera connection status appears below Pickup area.

**Manual sort (moves the arm)** appears below Home and Sorting in the bridge controls. It creates one button per `state.bins` entry, falling back to `PAPER`, `PLASTIC`, `METAL`, and `ORGANIC` if that field is missing. Buttons are enabled only while the API is online, the bridge is available, the controller is connected and `READY`, automatic sorting is paused, and neither a control command nor STOP confirmation is pending. Camera and Gemini availability do not gate manual sorting. A completed manual sort is recorded by the bridge with label `Manual`; rejected or unfinished commands do not change counts or history. Home, Sorting, and manual controls are hidden and cannot send commands in `?demo=1` sample preview.

Overview shows the camera, arm status, four bin totals, and recent completed sorts together. Their sidebar links open focused views of the same live components; returning to Overview restores them to the combined layout.

In the backend-served dashboard, **Settings → Choose a camera** selects the simulated camera, laptop/USB camera, or an ESP32-S3 JPEG snapshot URL. The ESP32 option expects a local URL such as `http://192.168.1.50/capture`, after the board has been programmed to serve snapshots on the same Wi-Fi network. See [XIAO ESP32-S3 camera setup](../docs/xiao-esp32s3-camera.md). Camera selection calls `POST /api/camera` and clears the prior frame and prediction. With a simulated arm, recognition and sorting resume automatically when vision is ready; physical-arm mode still needs an explicit Start sorting command. The API state includes `camera_source`, `camera_url`, `camera_error`, and `vision_ready`. The separate `?demo=1` sample preview does not operate camera controls.

## Dashboard checks

Run `node --test web/*.test.mjs` and `node --check web/app.mjs`. The app tests simulate delayed frame loads, in-flight state requests during STOP, unknown controller states, the STOP confirmation deadline, and completed-sort history updates.

Operator tests cover the public viewer/public operator/local access matrix, viewer STOP, successful login, incorrect passwords and rate limits, logout, immediate password clearing without browser storage, command `401` state refresh, delayed pre-login responses, viewer isolation in camera/Gemini modules, camera discovery after login, and hiding the panel in local/sample mode.

The STOP POST has an eight-second request timeout to allow for the controller's potentially slow stop operation. STOP confirmation is evaluated only from state requests begun after the POST succeeds, and only a connected `ERROR` state confirms it; `READY`, `DONE`, and unrecognized states do not. Home, Start sorting, and manual sort buttons remain disabled while STOP is being sent or awaiting confirmation. The five-second confirmation warning starts after a successful POST and runs independently of polling. On confirmation the dashboard says, "Arm stopped. Check it is clear, then press Home arm to recover." An ERROR state also displays the arm detail and Home recovery hint; disconnected states preserve connection-loss detail, and BUSY states show the running command. These checks do not replace testing with the backend and physical controller.
