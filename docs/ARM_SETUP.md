# Physical arm setup for the two-bin trial

This is the next step after the website demo. Do it on branch `66-recycle-trash-trial`. The running four-bin dashboard is a separate process; do not restart it onto this branch until the poses below are taught.

The website already sends `SORT RECYCLE` and `SORT TRASH`. The arm stays simulated until `arm/poses.json` has real positions and the bridge is started without `--mock-arm`.

Keep the servo power switch within reach. Hold the arm when torque turns off, because the shoulder drops. Paul owns the physical arm; check with him before the first powered move.

## 1. Find the USB port

Power the arm and plug the controller into this Mac. From the LeRobot SO-101 guide:

```sh
lerobot-find-port
```

The printed path is `ARM_PORT`, for example `/dev/tty.usbmodem585A0076841`.

## 2. Calibrate

```sh
lerobot-calibrate \
  --robot.type=so101_follower \
  --robot.port="$ARM_PORT" \
  --robot.id=sharkbyte_follower
```

Put every joint in the middle of its range, press Enter, then move each joint through its full range.

## 3. Teach the two bins

```sh
cd ~/Documents/Codex/sharkbyte-2026
export ARM_PORT='/the/port/from/step/1'
.venv/bin/python -m arm.teach --port "$ARM_PORT"
```

Save these names while the arm is in that position: `home`, `above_pick`, `pick`, `above_recycle`, `above_trash`, plus gripper `open` and `closed`. `list` should show none missing.

## 4. Run the website with the real arm

```sh
export ARM_PORT='/the/port/from/step/1'
export BRIDGE_OPERATOR_PASSWORD='a-long-password'
.venv/bin/python -m bridge --public-port 4174 --public-host abhis-macbook-air.taila950cf.ts.net
```

Leave off `--mock-arm`. Home the arm from the page before sorting. Reconnect Gemini and the iPhone camera after this start. One bin is not sorted again until the camera sees the pickup empty.
