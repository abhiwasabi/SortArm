# Sorting dashboard

This folder is the static dashboard for issue #15. It contains no backend or hardware control code.

## Preview with sample data

From the repository root, run:

```sh
python3 -m http.server 4173 --directory web
```

Open `http://localhost:4173/?demo=1`. The amber **DEMO MODE** badge and scenario buttons make sample data explicit. The Stop button changes only the sample state; it does not contact the ESP32 or API.

## Use with the laptop backend

Ana's backend should serve `web/` at the same origin as these endpoints:

| Endpoint | Purpose |
| --- | --- |
| `GET /api/state` | Current prediction, ESP32 state, connection, and confirmed counts |
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
  "prediction_at": "2026-11-07T05:30:00Z"
}
```

`prediction_at` is an optional ISO 8601 time for the latest *prediction*. If it is older than eight seconds, the dashboard hides that detection. Without it, the dashboard labels the value **Latest response**, never a live detection. It clears predictions when the API cannot be reached. Unknown items should use `label: "UNKNOWN"` and `bin: null`.

If the camera endpoint fails or no new frame loads for several seconds, the dashboard hides the last prediction and route. A successful Stop request means only that the request reached the backend; the dashboard waits for a controller state update and warns if none arrives.

The dashboard never increments counts itself. The backend should update counts only after the ESP32 reports DONE. For live use, open the backend's dashboard URL without `?demo=1`; serving this folder on a separate port will show the API as offline unless the backend also serves the API on that origin.
