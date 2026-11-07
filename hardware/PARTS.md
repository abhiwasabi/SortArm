# Hardware parts

Inventory is unverified. Paul (@PaulPio) checks each status as `have`, `need`, or `TBD` before the build. Quantities marked TBD must also be confirmed by Paul.

## Bill of materials

| Part | Quantity | Notes / link | Status |
| --- | --- | --- | --- |
| SO-101 follower printed parts | 1 set | [SO-ARM100](https://github.com/TheRobotStudio/SO-ARM100); choose one follower plate below. Individual parts are listed in that repository's README. | TBD |
| Waveshare ST3215-HS serial bus servo | 6 | Same footprint as stock STS3215; joint IDs below. | TBD |
| Hiwonder BusLinker V3 | 1 | CH341 USB driver; default 1 Mbps; 5–14 V input. Communication jumper: Servo + USB. | TBD |
| Waveshare bus-servo adapter (fallback) | TBD | Adapter from the stock SO-101 parts list; alternative if BusLinker communication fails. | TBD |
| 12 V supply | 1 | At least 5 A; more current capacity is safer. | TBD |
| Physical power switch | 1 | On the 12 V line into the BusLinker; cuts power to all servos. | TBD |
| USB cable | 1 | Laptop to BusLinker. | TBD |
| Table clamp | 2 | Arm mounting. | TBD |
| Phillips screwdriver #0 | 1 | Assembly tool. | TBD |
| Phillips screwdriver #1 | 1 | Assembly tool. | TBD |
| Labelled bins | 4 | PAPER, PLASTIC, METAL, ORGANIC. Size TBD; Paul measures and records it in POSITIONS.md. | TBD |
| Fixed overhead camera | 1 | Laptop/USB webcam or ESP32-S3 camera; choice TBD, Paul and Ana confirm. | TBD |
| Optional printed overhead camera mount | TBD | SO-ARM100: `Optional/Overhead_Cam_Mount_Webcam`. | TBD |
| Multimeter | 1 | Check connector pin order before connecting any servo; Yusuf verifies wiring. | TBD |
| Laptop software | 1 environment | Python 3.12, LeRobot 0.7.0 (`lerobot[feetech]`), and this repository's `arm/` package. | TBD |

Prices and unspecified part numbers: TBD; Paul confirms when sourcing parts.

## Servos and joint IDs

ST3215-HS specifications: 20 kg.cm at 12 V, 6–12.6 V input, 106 rpm, 360 degree magnetic encoder with 4096 steps, 2.4 A locked-rotor current, and 45.22 × 35 × 24.72 mm size. Factory settings are ID 1 and 1 Mbps.

| ID | Joint label |
| --- | --- |
| 1 | `shoulder_pan` |
| 2 | `shoulder_lift` |
| 3 | `elbow_flex` |
| 4 | `wrist_flex` |
| 5 | `wrist_roll` |
| 6 | `gripper` |

LeRobot's SO-101 driver expects model number 777 (STS3215). Whether the ST3215-HS reports 777 is unverified: TBD; Paul records the number shown by `python -m arm.check_ids --port COM5` before proceeding.

## Printed parts

Choose one plate file from SO-ARM100:

| Printer bed | Plate file |
| --- | --- |
| 220 × 220 mm | `STL/SO101/Follower/Ender_Follower_SO101.stl` |
| 205 × 250 mm | `STL/SO101/Follower/Prusa_Follower_SO101.stl` |

Settings from the upstream README:

- PLA+.
- 0.4 mm nozzle with 0.2 mm layers, or 0.6 mm nozzle with 0.4 mm layers.
- 15% infill.
- Supports everywhere except slopes over 45 degrees; no supports in horizontal screw holes.
- Check printer accuracy first using `STL/Gauges` (`Gauge_0` / `Gauge_tight_1`) against a servo.

Printer choice and gauge fit: TBD; Paul checks before printing the follower set.

## Power

Use a 12 V supply rated for at least 5 A. More current capacity is safer: each servo can draw 2.4 A stalled. The supply actually available and its rating are TBD; Yusuf checks the label and wiring before power-on.

Put a physical switch on the 12 V line into the BusLinker so it cuts every servo at once. Servo power never comes from USB.

Before plugging in a servo, Yusuf checks the board's 3-pin connector with a multimeter. GND / V+ / signal must match the servo cable. **12 V on the signal pin can destroy a servo.** See [ASSEMBLY.md](ASSEMBLY.md) for the wiring and first test.

## Credits and licences

- SO-101 arm design and STL files: TheRobotStudio with Hugging Face, [SO-ARM100](https://github.com/TheRobotStudio/SO-ARM100), Apache-2.0.
- LeRobot: Hugging Face, [LeRobot](https://github.com/huggingface/lerobot), Apache-2.0.

SharkByte allows open-source libraries and APIs but requires the core project to be made during the hackathon weekend. Credit the reused open arm design in the submission and confirm its use with the organizers. Organizer confirmation: TBD; Ana handles submission coordination.
