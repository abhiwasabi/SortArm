# SignSpeak MVP plan

## What we are building

A prototype that recognizes a small, selected set of ASL signs and turns a confirmed result into text and spoken audio. It is **selected-sign recognition to speech**, not full ASL translation or a replacement for a qualified interpreter.

**Live demo:** a signer makes one supported sign in front of the camera → the app shows its proposed label and confidence → the signer confirms or edits the text → the laptop speaks the approved text.

## Minimum scope

- Agree on 5–10 actual ASL signs with someone knowledgeable in ASL. Start with a smaller reliable set, then add more.
- Show `UNKNOWN` rather than guessing when the model is uncertain.
- Require a stable result across several frames. Never auto-speak an unconfirmed prediction.
- Use the Freenove ESP32-WROVER camera to stream to a laptop. A laptop webcam is a clearly labeled fallback. The separate ESP32-S3 can show status if time permits.
- The laptop handles recognition, the integration service, the web app, and speech playback.
- Do not store live video by default. Collect training examples only with the signer's consent.

## Architecture

```text
WROVER camera → laptop server → recognition.predict(frames)
                                  ↓
                      GET /api/prediction
                                  ↓
                    web app: preview + text
                                  ↓
                       signer confirms/edits
                                  ↓
                     browser text-to-speech
```

### Shared contract

- `recognition.predict(frames)` accepts a short time window of frames and returns `{label, confidence}`. The recognizer may use one frame for static signs, but the interface must allow movement over time.
- `label` is one of the agreed supported labels or `UNKNOWN`. `confidence` is a number from 0 to 1.
- `GET /api/status` reports camera and model readiness plus `READY`, `UNCERTAIN`, or `ERROR` for the ESP32-S3 status device.
- `GET /api/prediction` returns `{label, confidence, stable, timestamp}`, where `timestamp` is ISO 8601 and `stable` is a boolean.
- The web app must show an error if the camera or server disconnects; it must not keep showing a stale prediction.
- Post any contract change on the [Message Board](https://github.com/abhiwasabi/sharkbyte-2026/issues/1) before implementing it.

## Owners and GitHub issues

| Owner | Area | Issue |
| --- | --- | --- |
| @PaulPio | ESP32-WROVER camera firmware and stream (`firmware/camera/`) | [#3](https://github.com/abhiwasabi/sharkbyte-2026/issues/3) |
| @yusufdirdis | ESP32-S3 status device and 3D printed camera rig (`firmware/status/`, `hardware/`) | [#8](https://github.com/abhiwasabi/sharkbyte-2026/issues/8) |
| @abhiwasabi | Selected-sign recognition (`recognition/`) and web UI, confirmation, and speech (`web/`) | [#4](https://github.com/abhiwasabi/sharkbyte-2026/issues/4), [#5](https://github.com/abhiwasabi/sharkbyte-2026/issues/5) |
| @anaoliveira01 | Server, API, integration (`server/`, `scripts/`, `docs/`) and testing, demo, submission coordination | [#6](https://github.com/abhiwasabi/sharkbyte-2026/issues/6), [#7](https://github.com/abhiwasabi/sharkbyte-2026/issues/7) |

These are the agreed assignments. Post any handoff or scope change on the Message Board and update this table, `AGENTS.md`, and the relevant issue.

## Build order

1. Confirm the supported signs and the API contract. The UI and recognition owners can use mock frames/results immediately.
2. Make one end-to-end path work with a single sign: camera → model → app → confirmation → speech.
3. Add the remaining signs and handle `UNKNOWN`, disconnects, and repeated predictions.
4. Test with someone whose examples were not used for training. Record correct, incorrect, and uncertain results.
5. Rehearse the live demo under the actual lighting and network. Prepare a clearly labeled fallback clip and submit the project.

## UI checklist

- Camera preview and connection status
- Large proposed text and confidence/uncertainty indicator
- Confirm & Speak, Edit, Retry, and Stop/Mute controls
- Clear display of the approved phrase while it is spoken
- Simple setup instructions and accessible contrast/readable text

## Submission checklist

- Runnable README with setup, hardware, and start commands
- List of supported signs and observed test results
- Short live demo script, screenshots, and video
- Architecture diagram and honest limitations
- GitHub repository and Devpost submission
- Check the event's actual sponsor challenge sheet before claiming track eligibility

**Stretch goals only after the MVP works:** Azure Speech, a Base44 feedback dashboard, more signs, a physical Confirm/Speak or Mute button, or a better enclosure.
