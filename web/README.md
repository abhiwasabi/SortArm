# Sorting dashboard

This folder is the static dashboard for issue #15. It contains no backend or hardware control code.

## Preview with sample data

From the repository root, run:

```sh
python3 -m http.server 4173 --directory web
```

Open `http://localhost:4173/?demo=1`. This shows a fixed sample bottle and counts so the layout can be reviewed before the backend is running. The line under **Live sorting** identifies the sample preview. The Stop button does not contact hardware or the API in this preview.

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
  "label": "bottle",
  "confidence": 0.94,
  "bin": "RECYCLE",
  "arm_state": "READY",
  "connected": true,
  "counts": {"RECYCLE": 4, "TRASH": 2},
  "prediction_at": "2026-11-07T05:30:00Z",
  "recent_sorts": [
    {"id": "sort-6", "label": "bottle", "bin": "RECYCLE", "completed_at": "2026-11-07T05:29:52Z"},
    {"id": "sort-5", "label": "snack wrapper", "bin": "TRASH", "completed_at": "2026-11-07T05:28:11Z"}
  ]
}
```

`prediction_at` is an optional ISO 8601 time for the latest *prediction*. If it is older than eight seconds, the dashboard hides that detection. Without it, the dashboard labels the value **Latest response**, never a live detection. It clears predictions when the API cannot be reached. Unknown items should use `label: "UNKNOWN"` and `bin: null`.

`recent_sorts` is an optional, bounded list of completed sorts, newest first. Each entry has a stable `id`, the accepted item's `label`, its destination `bin`, and an ISO 8601 `completed_at` time. The backend should capture the label and bin when it accepts a sort, then append one entry when that command reports DONE, alongside incrementing the matching count. Predictions, rejected commands, STOP, and failed moves must not create history entries. If the field is omitted, the dashboard shows history as unavailable.

If the camera endpoint fails or no new frame loads for several seconds, the dashboard hides the last prediction and route. A successful Stop request means only that the request reached the backend; the dashboard waits for a controller state update and warns if none arrives.

The dashboard never increments counts itself. The backend should update counts only after the arm controller reports DONE. For live use, open the backend's dashboard URL without `?demo=1`; serving this folder on a separate port will show the API as offline unless the backend also serves the API on that origin. The Settings view shows the API and arm connection status, while the camera connection status appears below Pickup area.

Overview shows the camera, arm status, and bin totals together. Their sidebar links open focused views of the same live components; returning to Overview restores them to the combined layout.

## Dashboard checks

Run `node --test web/*.test.mjs` and `node --check web/app.mjs`. The app tests simulate delayed frame loads, in-flight state requests during STOP, unknown controller states, and the STOP confirmation deadline.

STOP status is evaluated only from state requests begun after the Stop POST succeeds. READY, DONE, or ERROR is displayed as the controller's reported state; an unrecognized state does not confirm STOP. The five-second warning runs independently of polling. These checks do not replace testing with the backend and physical controller.
