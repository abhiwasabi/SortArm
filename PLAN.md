# SignSpeak draft concept

This is a working concept for the fresh kickoff. Confirm the demo scope before creating new task issues.

## What we are building

A two-mode prototype for a small, validated set of ASL signs and phrases:

1. **Sign → Speech:** an outward-facing camera on a glasses frame sees a signer. The app proposes text, a person confirms or edits it, and a speaker says the approved phrase.
2. **Speech → Sign:** a microphone captures speech. The app transcribes it, matches a supported phrase, and shows animated signing hands with a caption in the web app. Unsupported speech gets an explicit unsupported result.

This is a selected-sign and selected-phrase prototype, not general ASL translation or a replacement for an interpreter. An ASL-knowledgeable person should review the supported signs and animations. Hand-only animation may omit facial and body information used in ASL.

## Minimum scope

- Start with 3–5 validated concepts that can be demonstrated in both directions.
- Show UNKNOWN rather than guessing when sign recognition is uncertain. Require a stable result across a short frame window.
- Never speak an unconfirmed prediction or invent an animation for an unsupported phrase.
- Attach a camera to an existing glasses frame and test the view at normal conversation distance. No 3D printed mount is needed.
- Use the available ESP32 camera if it works on the frame; document a camera fallback if it does not. The recognition and web app can run on a laptop or phone.
- Use a speaker that can be heard in the demo. Its final location and connection are still to be chosen.
- The servos are working and available for a later scope decision. The agreed Speech → Sign output is animated hands in the web app.
- Do not store live video by default. Collect training examples only with consent.

## Architecture

**Sign → Speech:** glasses camera → frame stream → recognition on the host → proposed text in web app → confirm or edit → speech through speaker.

**Speech → Sign:** microphone → speech transcript → supported-phrase match → animated hands and caption in web app.

### Shared contract

- recognition.predict(frames) accepts a short time window and returns a sign ID and confidence. It may return UNKNOWN.
- GET /api/status reports camera and model readiness.
- GET /api/prediction returns {label, confidence, stable, timestamp}; timestamp uses ISO 8601.
- A shared catalog maps supported IDs to spoken phrases, accepted transcript variants, animation assets, and validation status.
- The app must show disconnect or uncertainty clearly and must not display a stale prediction as current.
- Post interface changes on the [Message Board](https://github.com/abhiwasabi/sharkbyte-2026/issues/1).

## Team roles

Roles are intentionally blank until the team sets them.

| Teammate | Role | Issues |
| --- | --- | --- |
| @abhiwasabi |  |  |
| @PaulPio |  |  |
| @anaoliveira01 |  |  |
| @yusufdirdis |  |  |

The earlier task issues #2–#9 were closed as superseded. [Fresh kickoff #10](https://github.com/abhiwasabi/sharkbyte-2026/issues/10) is the only open planning issue. Create new implementation issues after the team confirms the demo and fills the role table.

## Suggested build order

1. Confirm the first supported concepts and the sign, transcript, and animation catalog.
2. Test the camera view from the glasses frame before collecting model examples.
3. Make one complete Sign → Speech path work, then one Speech → Sign phrase and animation.
4. Add the remaining supported concepts and handle UNKNOWN, unsupported speech, and disconnects.
5. Test with an unseen signer and rehearse both directions under actual lighting, power, audio, and network conditions.

## UI checklist

- Clear mode switch between Sign → Speech and Speech → Sign
- Camera preview, connection status, proposed text, and uncertainty display
- Confirm & Speak, Edit, Retry, and Stop/Mute controls
- Microphone control, transcript, supported-phrase feedback, animated hands, and caption
- Readable controls and layout on the chosen demo screen

## Submission checklist

- Runnable README with setup, hardware, and start commands
- Supported sign and phrase list with observed test results
- Short demo script, screenshots, and video showing both modes
- Architecture diagram and honest limitations
- GitHub repository and Devpost submission
- Check the event's current sponsor challenges before claiming track eligibility
