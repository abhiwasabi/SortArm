# Arm control (SO-101)

Laptop code that moves the SO-101 follower arm for the sorter demo (issue #20). It wraps [LeRobot](https://github.com/huggingface/lerobot) in the team's arm contract: `HOME`, `SORT RECYCLE`, `SORT TRASH`, `STOP` → `READY`, `BUSY`, `DONE`, `ERROR`. See [ARCHITECTURE.md](../ARCHITECTURE.md) for how it fits with vision, the bridge, and the dashboard.

## Install (once per laptop)

From the repository root, with Python 3.12:

```sh
py -3.12 -m venv .venv                       # macOS/Linux: python3.12 -m venv .venv
.venv\Scripts\activate                       # macOS/Linux: source .venv/bin/activate
pip install -r arm/requirements.txt
```

The commands below assume the venv is active and you are in the repository root.

## Try it without hardware

```sh
python -m pytest arm/tests
python -m arm.cli --mock
```

In the console, type `HOME`, then `SORT RECYCLE`, `SORT TRASH`, and `STOP`. The mock uses made-up poses until `poses.json` is taught.

## Hardware setup

### 1. Find the port

Plug the Waveshare board into USB and its power supply, then run:

```sh
lerobot-find-port
```

On Windows the port looks like `COM5`. Set it for the session:

```powershell
$env:ARM_PORT = "COM5"        # PowerShell; bash: export ARM_PORT=COM5
```

### 2. Set servo IDs — before assembly

Each STS3215 ships with the same ID, so every servo needs its own ID (1–6) **before it goes into the printed parts**. Connect one servo at a time when prompted:

```sh
lerobot-setup-motors --robot.type=so101_follower --robot.port=COM5
```

Label each servo with its joint: 1 shoulder_pan, 2 shoulder_lift, 3 elbow_flex, 4 wrist_flex, 5 wrist_roll, 6 gripper. Assembly: [SO-101 guide](https://huggingface.co/docs/lerobot/so101).

### 3. Calibrate (assembled arm)

Save the calibration in this repository, so every teammate's laptop can drive the same arm:

```sh
lerobot-calibrate --robot.type=so101_follower --robot.port=COM5 --robot.id=sharkbyte_follower --robot.calibration_dir=arm/calibration
```

Follow the prompts. First put every joint in the middle of its range, then move each joint through its full range. This writes `arm/calibration/sharkbyte_follower.json`. Commit it. Re-run it if a servo or horn is replaced.

### 4. Teach the poses

Mark the pickup spot and both bins on the table first. Then:

```sh
python -m arm.teach
```

Hold the arm when torque turns off. Move it by hand to each position and type the name:

| Name | Position |
| --- | --- |
| `home` | Safe rest pose clear of the camera view. Ideally the arm rests stably here, since closing the programs turns torque off. |
| `above_pick` | A few cm above the item at the pickup spot, jaws open |
| `pick` | Jaws around the item |
| `above_recycle` | Over the RECYCLE bin opening |
| `above_trash` | Over the TRASH bin opening |
| `open` / `closed` | Gripper opening: wide enough for the biggest item / fully closed |

Type `list` to see what is missing and `t` to toggle torque. The poses are saved to `arm/poses.json`. Commit it with a note of what was tested.

### 5. Bench test

```sh
python -m arm.cli --speed 15
```

Keep a hand on the servo power switch. Run `HOME`, then `SORT RECYCLE` and `SORT TRASH` with no item, then with each demo item. Try `STOP` in the middle of a move. Raise the speed (default 20 °/s, or `ARM_SPEED_DEG_S`) only after the poses are clean. Record the results in the PR.

## Settings

Set these as environment variables; defaults are in `config.py`.

| Variable | Default | Meaning |
| --- | --- | --- |
| `ARM_PORT` | — | Serial port of the Waveshare board |
| `ARM_ID` | `sharkbyte_follower` | Calibration file name in `arm/calibration/` |
| `ARM_SPEED_DEG_S` | `20` | Body joint speed |
| `ARM_GRIPPER_SPEED` | `80` | Gripper speed (units of 0–100 per second) |
| `ARM_MAX_RELATIVE_TARGET` | `10` | Largest single step LeRobot allows for a body joint, in degrees |
| `ARM_POSES` | `arm/poses.json` | Poses file |

## Troubleshooting

- **`No calibration at …`**: run step 3, or check `ARM_ID`.
- **`The motors do not match …`**: the calibration file is from a different arm or servo; re-run step 3.
- **`… did not reach its goal`**: the joint is blocked, overloaded, or the pose is out of reach. The arm holds, and HOME recovers it. Check for collisions and re-teach the pose.
- **Port busy**: close any other program using the port (another console, `lerobot-*` tools).
