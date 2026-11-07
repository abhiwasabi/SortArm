# Table positions and taught poses

This is a measurement and trial worksheet, not a verified layout. Paul (@PaulPio) measures the layout and teaches poses; Ana (@anaoliveira01) records bench-test results with Paul. Leave unmeasured values as TBD.

## Coordinate convention

Use centimetres from the base axis: x forward, y left, z up from the table. Paul marks these directions on the table before measuring.

## Layout measurements

| Location | x (cm) | y (cm) | Bin size (cm) | Bin height (cm) |
| --- | --- | --- | --- | --- |
| Pickup spot | TBD | TBD | TBD | TBD |
| PAPER | TBD | TBD | TBD | TBD |
| PLASTIC | TBD | TBD | TBD | TBD |
| METAL | TBD | TBD | TBD | TBD |
| ORGANIC | TBD | TBD | TBD | TBD |

Paul measures every TBD above, including pickup-area dimensions and pickup surface height. Physical reach and all four bin openings must be verified with the taught poses before claiming the hardware sorts all four materials.

## Layout tool output

From the repository root with the Python environment active:

```sh
python -m arm.layout suggest --pickup 20,0 --bin-width 15 --bin-height 15 --item-height 6
python -m arm.layout check --pickup X,Y --bin PAPER=X,Y --bin PLASTIC=X,Y --bin METAL=X,Y --bin ORGANIC=X,Y --bin-height 15 --item-height 6
```

Replace each `X,Y` with the measured centre in cm, and the sizes with the measured ones. Add `--json` for machine-readable output. With the defaults, `suggest` places the bins on a 36 cm arc at about ±20° and ±60° from straight ahead: PAPER 18,-31.2; PLASTIC 33.8,-12.3; METAL 33.8,12.3; ORGANIC 18,31.2. The pickup approach point (11 cm above a 6 cm item) is close to the wrist's limit, so keep the pickup spot near 20 cm in front of the base.

Suggestion output: TBD; Paul pastes it here.

Measured check command and output: TBD; Paul pastes both here.

Layout decision and any changes after checking: TBD; Paul records them here.

## Taught poses

After `python -m arm.teach`, Paul copies values from `arm/poses.json`. Body joints use degrees; the gripper uses 0–100. Do not fill this table with guessed joint values. The `open` / `closed` rows record gripper settings; body joint cells are not applicable for those settings.

| Pose / setting | shoulder_pan | shoulder_lift | elbow_flex | wrist_flex | wrist_roll | gripper |
| --- | --- | --- | --- | --- | --- | --- |
| `home` | TBD | TBD | TBD | TBD | TBD | TBD |
| `above_pick` | TBD | TBD | TBD | TBD | TBD | TBD |
| `pick` | TBD | TBD | TBD | TBD | TBD | TBD |
| `above_paper` | TBD | TBD | TBD | TBD | TBD | TBD |
| `above_plastic` | TBD | TBD | TBD | TBD | TBD | TBD |
| `above_metal` | TBD | TBD | TBD | TBD | TBD | TBD |
| `above_organic` | TBD | TBD | TBD | TBD | TBD | TBD |
| `open` (gripper) | — | — | — | — | — | TBD |
| `closed` (gripper) | — | — | — | — | — | TBD |

Teaching date and person: TBD; Paul records them. Verification of the four `above_<bin>` poses: TBD; Paul checks each physical opening.

## Photo checklist

Paul captures the build photos; Ana selects submission evidence. Photo locations: TBD; Ana records them after capture.

- [ ] Whole table with base, marked pickup spot, and all four labelled bins.
- [ ] Base-axis origin and x/y direction marks beside the measured layout.
- [ ] Fixed overhead camera and its view of the pickup spot.
- [ ] BusLinker jumper, checked connector pin order, and servo ID labels.
- [ ] 12 V supply label and reachable physical power switch.
- [ ] Arm at `home`, `above_pick`, and `pick`.
- [ ] Arm above each of PAPER, PLASTIC, METAL, ORGANIC.

## Bench-test log

Start with `python -m arm.cli --speed 15` and keep a hand on the power switch. For each SORT row, record trials without an item and with each demo item. STOP should freeze the arm and keep the gripper's grip. Support the arm when exiting: torque turns off.

| Command / trial | Result | Notes |
| --- | --- | --- |
| `HOME` | TBD | TBD |
| `SORT PAPER` | TBD | TBD |
| `SORT PLASTIC` | TBD | TBD |
| `SORT METAL` | TBD | TBD |
| `SORT ORGANIC` | TBD | TBD |
| `STOP` mid-move | TBD | TBD |

Trial date, operator, demo items, and any re-teaching: TBD; Paul and Ana record them with the results.
