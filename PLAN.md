# Robotic Waste Sorter — working plan

This trial branch (issue #66) places a recognized item in one of two bins: **Recycle or Trash**. `main` still uses Paper, Plastic, Metal, and Organic. Confirm the exact objects and arm geometry in [kickoff issue #10](https://github.com/abhiwasabi/sharkbyte-2026/issues/10) before final printing and model setup.

## Live demo

1. Place one dry, lightweight item in a marked pickup area.
2. A fixed camera sends frames to the laptop's vision component. The team is still settling the model and decision rule in #14 and #16.
3. A documented decision maps clean paper, plastic, and metal packaging to RECYCLE, and food scraps or ordinary waste to TRASH. Unknown or uncertain items do not start an automatic sort.
4. The laptop asks the arm controller to pick the item and move to the taught position above its bin, release it, and return home. Paul is building the SO-101 arm controller in #20; the ESP32-S3's proposed role is a physical stop/status accessory, pending team agreement.
5. The dashboard shows the camera view, detected item, chosen bin, arm state, confirmed counts, and recent completed sorts.

Choose at least one item for each bin that the vision component can identify and the gripper can handle. Keep the pickup area and both bin positions fixed. The system demonstrates recognition and sorting; an image alone cannot establish an item's exact composition or local disposal eligibility.

## Architecture

**Camera → laptop vision and bin decision → laptop bridge → SO-101 arm controller → gripper → recycle or trash.**

- Use a fixed overhead camera so its view does not move with the arm. First get frames from a laptop webcam or USB camera. An ESP32 camera stream is an option if it is stable and available.
- Run the selected vision model on the laptop. Keep its output contract stable while #14 and #16 settle the model and confidence meaning. A numeric confidence may be absent, especially for a model whose self-reported score is not a calibrated probability.
- The planned SO-101 arm control in #20 runs on the laptop through a USB bus-servo adapter. An ESP32-S3 stop/status accessory is proposed but is not required to choose a bin or drive the arm.
- Teach and test home, pickup, and two physical drop positions: `above_recycle` and `above_trash`. Move slowly and verify reach and collision clearance before using real items. Power the servos from an appropriate external supply.
- Put an emergency stop or immediate stop command within reach during testing and the demo.

### Sorting contract

- The supported bin names on this branch are `RECYCLE` and `TRASH`. The laptop bridge calls the arm controller with one of these names or issues no SORT for an unknown item. Its command form is `SORT <BIN>`, alongside `HOME` and `STOP`. Both names and their `above_<bin>` poses must be taught before physical sorting works. The bridge must apply its uncertainty and safety gate before dispatching SORT; the dashboard may display a valid backend bin decision even when `confidence` is null.
- The arm reports `READY`, `BUSY`, `DONE`, or `ERROR`. It accepts SORT only while READY. The bridge updates a bin count only after a completed SORT reports DONE.
- The laptop backend exposes `GET /api/state` with `label`, `confidence`, `bin` (`RECYCLE`, `TRASH`, or null), `arm_state`, `connected`, and `counts` with RECYCLE and TRASH keys. It may also expose `prediction_at` and `recent_sorts` for completed items. The dashboard also uses `GET /api/frame.jpg` and `POST /api/stop`. Ana owns the bridge contract and coordinates changes with Abhi and Paul.
- Document the supported item types, their bin rules, and how they were tested. Post interface changes on the [Message Board](https://github.com/abhiwasabi/sharkbyte-2026/issues/1).

## Build order

1. Choose safe demo items for RECYCLE and TRASH, plus camera position, arm reach, and gripper shape. Measure the pickup spot and both bin distances before fixing the layout.
2. Assemble the SO-101 arm and place the two labeled bins where it can reach them. Test gripper fit and servo power with the chosen items.
3. Teach home, pickup, and both `above_<bin>` positions. Manually test each path and STOP before enabling automatic sorting.
4. Make the camera and laptop vision component classify the chosen items under demo lighting. Check confusing items and ensure unsupported or uncertain results have no automatic bin command.
5. Connect vision, bridge, and arm controller. Complete one camera-to-bin path, then verify each remaining category.
6. Check dashboard counts and per-item history against completed drops. Rehearse repeated cycles, failures, recovery, lighting, power, and a short live explanation.

If the camera or model takes longer than expected, keep the arm demo running with clearly labeled manual bin buttons while continuing model work. The final presentation should state which steps are automated.

## Team roles

These are the current owners. Each linked issue has a concrete task and acceptance criteria.

| Teammate | Role | Issues |
| --- | --- | --- |
| @abhiwasabi | Vision and app | [#14](https://github.com/abhiwasabi/sharkbyte-2026/issues/14), [#15](https://github.com/abhiwasabi/sharkbyte-2026/issues/15), [#23](https://github.com/abhiwasabi/sharkbyte-2026/issues/23) |
| @PaulPio | Arm mechanics and control | [#12](https://github.com/abhiwasabi/sharkbyte-2026/issues/12), [#20](https://github.com/abhiwasabi/sharkbyte-2026/issues/20) |
| @anaoliveira01 | Integration and testing | [#16](https://github.com/abhiwasabi/sharkbyte-2026/issues/16), [#17](https://github.com/abhiwasabi/sharkbyte-2026/issues/17) |
| @yusufdirdis | Electronics and firmware | [#13](https://github.com/abhiwasabi/sharkbyte-2026/issues/13) |

The earlier sign-language task issues #2–#9 were closed as superseded. [Kickoff #10](https://github.com/abhiwasabi/sharkbyte-2026/issues/10) tracks shared decisions. The original issues #12–#17 still describe the earlier two-bin/ESP32 plan in places; #20 tracks the laptop-driven arm and #23 tracks the four-bin change. Owners should update their issue contracts as their work lands.

## Demo and submission checklist

- Video and live demo of a supported item going from camera recognition to the correct bin for each of the four categories
- On-screen item, confidence when available, bin decision, and arm status, including an unknown-item example
- Wiring and power diagram, parts list, and arm design files
- Setup commands, supported object list, bin rules, and observed test results
- Short explanation of limitations: controlled pickup area, selected objects, and local recycling rules
- Repository attached to the [SharkByte 2026 Devpost submission](https://sharkbyte2026.devpost.com/) before its listed deadline
- Document external libraries, pretrained models, and reused assets as required by the [event rules](https://sharkbyte2026.devpost.com/rules)

The project is a natural candidate for the event's **Best Use of Hardware** prize. Prize selection is not guaranteed; check the current Devpost page and ask organizers about any additional track rules.
