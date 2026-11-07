# Arm assembly and bring-up

Paul leads assembly, IDs, calibration, and poses. Yusuf verifies power and connector wiring. Ana records end-to-end trials. Unknown measurements and results stay TBD until the responsible person checks them.

Commands assume an active Python 3.12 environment and the repository root, as in [arm/README.md](../arm/README.md). `COM5` is the example port; use the port found on the build laptop. These instructions do not record completed hardware tests.

## Wiring

```text
12 V supply -> power switch -> BusLinker V3 power input
BusLinker servo port -> servo 1 (shoulder_pan)
                    -> daisy chain through servos 2..6 -> servo 6 (gripper)
BusLinker USB -> laptop
```

Servo power never comes from USB. Before connecting a servo, check GND / V+ / signal with a multimeter against the servo cable. 12 V on the signal pin can destroy a servo. Put the communication jumper on Servo + USB.

## Setup order

1. **Find the port.** Install the CH341 USB driver, connect the BusLinker USB and 12 V supply, and identify the port.

   ```sh
   lerobot-find-port
   ```

   Set the session port as in the arm README:

   ```powershell
   $env:ARM_PORT = "COM5"        # PowerShell; bash: export ARM_PORT=COM5
   ```

   Worked when: Paul has identified the board's port (for example, COM5).

2. **Test one servo first.** Follow arm/README.md step 0. Yusuf checks the jumper and measures the powered board's connector pin order before a servo is plugged in. Power off, connect one servo, then power on. Use only the gripper prompt for this first test.

   ```sh
   lerobot-setup-motors --robot.type=so101_follower --robot.port=COM5
   ```

   Worked when: the tool prints `'gripper' motor id set to 6`. If the model differs, Paul records the reported number; compatibility is TBD until verified. If no motor is found at any baud rate after checking jumper, pin order, and power, use the fallback Waveshare bus-servo adapter from the stock parts list.

3. **Set IDs before assembly.** Connect only one servo at a time when prompted. Label each servo with its ID and joint name.

   ```sh
   lerobot-setup-motors --robot.type=so101_follower --robot.port=COM5
   ```

   Worked when: all six servos are labelled 1 `shoulder_pan`, 2 `shoulder_lift`, 3 `elbow_flex`, 4 `wrist_flex`, 5 `wrist_roll`, 6 `gripper`.

4. **Verify the bus.** Connect the assigned servos and inspect IDs and model numbers.

   ```sh
   python -m arm.check_ids --port COM5
   ```

   Worked when: IDs 1–6 answer with the intended labels, and Paul has checked the reported model against LeRobot's expected 777. ST3215-HS model compatibility remains TBD until measured; do not assume a different model is supported.

5. **Assemble the follower.** Follow the [Hugging Face SO-101 guide](https://huggingface.co/docs/lerobot/so101), using the labelled servos, printed follower parts, and two table clamps.

   Command: none; this is a physical assembly step.

   Worked when: Paul has checked the assembly against the guide and the ID labels match the installed joints. Assembly result: TBD; Paul verifies.

6. **Calibrate the assembled arm.** Follow the prompts: put every joint in the middle of its range, then move each through its full range. Support the arm when torque is off.

   ```sh
   lerobot-calibrate --robot.type=so101_follower --robot.port=COM5 --robot.id=sharkbyte_follower --robot.calibration_dir=arm/calibration
   ```

   Worked when: calibration finishes and writes `arm/calibration/sharkbyte_follower.json`. Recalibrate if a servo or horn is replaced.

7. **Plan the table layout.** Get a suggested arc for the four bins, then check the positions you actually mark (centimetres from the base axis; x forward, y left).

   ```sh
   python -m arm.layout suggest --pickup 20,0 --bin-width 15 --bin-height 15
   python -m arm.layout check --pickup 20,0 --bin PAPER=18,-31.2 --bin PLASTIC=33.8,-12.3 --bin METAL=33.8,12.3 --bin ORGANIC=18,31.2
   ```

   The positions above are what `suggest` gives for 15 cm bins, 15 cm tall, with the pickup spot 20 cm in front of the base. Use your measured bin size, bin height and item height instead. `check` exits 0 only if every point is reachable. The gripper may tilt up to 15° at the pickup and 60° over a bin (`--pick-tilt`, `--drop-tilt`): the SO-101 can't point straight down that high. This is a planning estimate from the official model; the real positions come from teaching in step 9.

   Worked when: `check` reports every point reachable for the measured layout, and Paul has pasted the output into [POSITIONS.md](POSITIONS.md). Current output and result: TBD.

8. **Mark pickup and bins.** Mark the pickup spot and PAPER, PLASTIC, METAL, ORGANIC bin positions on the table. Record measurements in POSITIONS.md. Keep the camera fixed.

   Command: none; measure and mark the physical layout.

   Worked when: Paul has recorded all five positions, bin sizes and heights, and verified all four physical bin positions before claiming four-material sorting. Measurements: TBD.

9. **Teach poses.** Support the arm when torque turns off. Move it by hand to each named position and save it.

   ```sh
   python -m arm.teach
   ```

   Worked when: `list` shows no missing poses and `arm/poses.json` contains `home`, `above_pick`, `pick`, `above_paper`, `above_plastic`, `above_metal`, `above_organic`, and gripper `open` / `closed`. Copy measured values into POSITIONS.md. Use `t` to toggle torque.

10. **Bench test slowly.** Keep a hand on the servo power switch.

    ```sh
    python -m arm.cli --speed 15
    ```

    Run `HOME`, `SORT PAPER`, `SORT PLASTIC`, `SORT METAL`, and `SORT ORGANIC` with no item, then with each demo item. Try `STOP` mid-move.

    Worked when: Paul and Ana have recorded each result in POSITIONS.md, confirmed the four taught bin poses, and checked STOP freezes the arm while keeping the gripper's grip. Results: TBD; no hardware sorting claim until verified.

## Safety

- Keep a hand on the physical power switch during tests.
- Start slow with `--speed 15`.
- STOP freezes the arm and keeps the gripper's grip.
- Exiting arm programs turns torque off: the arm goes limp. Support it or end at rest.
- Never put fingers in the gripper while powered.

## Troubleshooting

| Symptom | What to check / do |
| --- | --- |
| No servo answers | Check 12 V power, Servo + USB jumper, and connector pin order. If correct but no motor is found at any baud rate, try the fallback Waveshare adapter. Yusuf checks wiring; Paul repeats the one-servo test. |
| Only ID 1 answers | Factory ID is 1 on every servo. Paul repeats ID setup one servo at a time, labels each, then verifies IDs 1–6. |
| Wrong model number | LeRobot expects 777. Paul records the number shown by `python -m arm.check_ids --port COM5` and posts it on the Message Board so `arm/` can be adapted; ST3215-HS support is unverified. |
| A joint does not reach its goal | Check for blockage, overload, or an unreachable pose. The arm holds; HOME recovers it. Paul checks collisions and re-teaches the pose before another slow trial. |
