# Sorting dashboard

This folder is the static dashboard for issue #15. It contains no backend or hardware control code.

## Preview with sample data

From the repository root, run:

```sh
python3 -m http.server 4173 --directory web
```

Open `http://localhost:4173/?demo=1`. This shows illustrative items, counts, and history for the four material bins so the layout can be reviewed before the backend is running. The line under **Live sorting** identifies the sample preview. The Stop button does not contact hardware or the API in this preview.

## Use with the laptop backend

Ana's backend should serve `web/` at the same origin as these endpoints:

| Endpoint | Purpose |
| --- | --- |
| `GET /api/state` | Current prediction, arm state, connection, confirmed counts, and optional recent sort history |
| `GET /api/frame.jpg` | Latest pickup-camera JPEG frame |
| `POST /api/stop` | Request STOP; the dashboard waits for state before claiming the arm stopped |

Example state:

```json
{
  "label": "plastic bottle",
  "confidence": 0.94,
  "bin": "PLASTIC",
  "arm_state": "READY",
  "connected": true,
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

Overview shows the camera, arm status, four bin totals, and recent completed sorts together. Their sidebar links open focused views of the same live components; returning to Overview restores them to the combined layout.

In the backend-served dashboard, **Settings → Choose a camera** selects the simulated camera, laptop/USB camera, or an ESP32-S3 JPEG snapshot URL. The ESP32 option expects a local URL such as `http://192.168.1.50/capture`, after the board has been programmed to serve snapshots on the same Wi-Fi network. See [XIAO ESP32-S3 camera setup](../docs/xiao-esp32s3-camera.md). Camera selection calls `POST /api/camera`, pauses sorting, and clears the prior frame and prediction. The API state includes `camera_source`, `camera_url`, `camera_error`, and `vision_ready`; the dashboard disables Start sorting when a real camera is only in preview mode. The separate `?demo=1` sample preview does not operate camera controls.

## Dashboard checks

Run `node --test web/*.test.mjs` and `node --check web/app.mjs`. The app tests simulate delayed frame loads, in-flight state requests during STOP, unknown controller states, the STOP confirmation deadline, and completed-sort history updates.

STOP status is evaluated only from state requests begun after the Stop POST succeeds. READY, DONE, or ERROR is displayed as the controller's reported state; an unrecognized state does not confirm STOP. The five-second warning runs independently of polling. These checks do not replace testing with the backend and physical controller.
