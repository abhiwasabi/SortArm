# Robotic Waste Sorter — working plan

The team has chosen a 3D printed robotic arm that recognizes a demo item and places it in a labeled bin. This is the current project direction. Confirm the exact object set, bin labels, and arm design in [kickoff issue #10](https://github.com/abhiwasabi/sharkbyte-2026/issues/10) before creating implementation issues.

## Live demo

1. Place one dry, lightweight item in a marked pickup area.
2. A fixed camera shows the item to a YOLO model running on a laptop.
3. A simple, documented rule maps a recognized item type to a demo bin. Low confidence or unknown items pause for human review.
4. The laptop sends a command to the ESP32-S3. The 3D printed arm follows tested pickup and drop positions, releases the item into the bin, and returns home.
5. A small dashboard shows the camera view, detected item, confidence, chosen bin, arm state, and counts.

Aim for two bins and 2–3 item types that the chosen model can actually recognize. Pick objects that are easy for the gripper to handle. Keep the pickup area and bin positions fixed. The system demonstrates object recognition and sorting; an image alone cannot prove that an item is clean or accepted by a local recycling program.

## Architecture

**Camera → laptop YOLO → item-to-bin rule → command → ESP32-S3 → servos/gripper → bin.**

- Use a fixed overhead camera so its view does not move with the arm. First get frames from a laptop webcam or USB camera. An ESP32 camera stream is an option if it is stable and available.
- Run YOLO inference on the laptop. The ESP32-S3 handles arm control, sensors if needed, and status. On-device model inference is a stretch goal only after the full loop works.
- Start with USB serial between laptop and ESP32-S3 for a reliable demo. Wi-Fi can be added later if needed.
- Use saved arm positions for home, pickup, and each bin; move slowly and test collision clearance. Give the servos an appropriate external power supply and share ground with the controller.
- Put an emergency stop or immediate stop command within reach during testing and the demo.

### Initial command contract

- Laptop sends `SORT RECYCLE` or `SORT TRASH` only when the arm reports `READY` and the model passes the chosen confidence threshold. Rename the bins if the team chooses different categories.
- ESP32-S3 reports `READY`, `BUSY`, `DONE`, or `ERROR` so the dashboard can show the real arm state. An unknown item produces no motion command.
- Document the supported item types, their bin rules, and how they were tested. Post interface changes on the [Message Board](https://github.com/abhiwasabi/sharkbyte-2026/issues/1).

## Build order

1. Choose the exact 2–3 demo items, two bin labels, camera position, arm reach, and gripper shape. Measure the pickup and bin distances before printing.
2. Print and assemble the smallest arm that can reach the marked pickup area and both bins. Test gripper fit and power with the chosen items.
3. Make the arm execute a repeatable manual sequence: home → pick → bin A → home, then bin B. Record tested positions and add a stop command.
4. Make the camera and laptop model recognize the chosen items under demo lighting. Check confusing items and select a confidence threshold. Add an `UNKNOWN` result.
5. Connect the laptop to the ESP32-S3. Complete one camera-to-bin path, then the second bin.
6. Add the dashboard and counts. Rehearse repeated cycles, failures, recovery, lighting, power, and a short live explanation.

If the camera or model takes longer than expected, keep the arm demo running with clearly labeled manual bin buttons while continuing model work. The final presentation should state which steps are automated.

## Team roles

Roles are intentionally blank until the team sets them.

| Teammate | Role | Issues |
| --- | --- | --- |
| @abhiwasabi |  |  |
| @PaulPio |  |  |
| @anaoliveira01 |  |  |
| @yusufdirdis |  |  |

The earlier sign-language task issues #2–#9 were closed as superseded. [Kickoff #10](https://github.com/abhiwasabi/sharkbyte-2026/issues/10) is the open planning issue. Create focused implementation issues after agreeing on scope and assignments. The work needs mechanical design/printing, ESP32 firmware and arm control, camera/YOLO, laptop-to-ESP32 integration, dashboard, testing, and submission materials.

## Demo and submission checklist

- Video and live demo of one item going from camera recognition to the correct bin, repeated for both bins
- On-screen item, confidence, bin decision, and arm status, including an unknown-item example
- Wiring and power diagram, parts list, and arm design files
- Setup commands, supported object list, bin rules, and observed test results
- Short explanation of limitations: controlled pickup area, selected objects, and local recycling rules
- Repository attached to the [SharkByte 2026 Devpost submission](https://sharkbyte2026.devpost.com/) before its listed deadline
- Document external libraries, pretrained models, and reused assets as required by the [event rules](https://sharkbyte2026.devpost.com/rules)

The project is a natural candidate for the event's **Best Use of Hardware** prize. Prize selection is not guaranteed; check the current Devpost page and ask organizers about any additional track rules.
