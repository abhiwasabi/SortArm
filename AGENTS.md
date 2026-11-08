# SharkByte 2026 team rules

## Team

- @abhiwasabi — teammate
- @PaulPio — teammate
- @anaoliveira01 — teammate
- @yusufdirdis — teammate

## GitHub issue board

The pinned **Message Board** issue is for short coordination messages between people and coding agents. Use `tools/board`; it needs GitHub CLI (`gh`) authenticated with your own GitHub account. If the board is not issue #1, set `BOARD_ISSUE` to its number.

- Start every post with `[<human>'s <agent>]`, such as `[Abhi's Codex]`.
- Mention a teammate by GitHub username. Use `@all` when everyone should see a post. `@all` is a board convention, so each agent must poll the board; it is not a GitHub notification group.
- Keep posts concrete: what changed, which files or interfaces are affected, and what decision or handoff you need.
- Check `tools/board unread` at the start of a task, before each commit, and after merges. `tools/board read -n 20` shows recent context. `tools/board wait` polls until another account posts; it does not wake a closed coding-agent session.
- If a post mentions you or `@all`, act on it and reply. Otherwise read it and adjust your work only if affected. Do not reply just to acknowledge.

## Work ownership

The current ownership split is recorded here, in [PLAN.md](PLAN.md), and in the issue assignees. Areas describe responsibility; implementation folders may be created as work begins. Coordinate overlapping files and interface changes on the board.

| Teammate | Role | Areas or folders |
| --- | --- | --- |
| @abhiwasabi | Vision and app | Vision prediction, item-to-bin rules, web dashboard |
| @PaulPio | Arm mechanics and control | CAD/3D printing, gripper, four-bin layout, SO-101 arm controller and poses |
| @anaoliveira01 | Integration and testing | Laptop bridge, app state, end-to-end trials, submission prep |
| @yusufdirdis | Electronics and firmware | Servo power/wiring, stop logic; proposed ESP32-S3 stop/status accessory |

## Current material-bin contract

This trial branch, [issue #66](https://github.com/abhiwasabi/sharkbyte-2026/issues/66), uses two destinations: `RECYCLE` and `TRASH`. Use exactly these uppercase names for predictor and bridge bin decisions, `/api/state.counts` keys, completed-sort history bins, and arm SORT destinations. Paper, plastic packaging, and metal cans are `RECYCLE`. Food scraps and other ordinary waste are `TRASH`. Unknown or unsupported items have no route (`null`). Taught poses are `above_recycle` and `above_trash`. The four-bin contract remains on `main` until this trial is accepted. See [docs/ARM_SETUP.md](docs/ARM_SETUP.md) for the physical arm steps.

## Git workflow

1. Pick or file an issue for each task. Comment when you take it.
2. Branch from current `main` using `<issue-number>-<short-name>`.
3. Stage explicit paths for your issue and coordinate before editing overlapping files.
4. Open a pull request referencing the task issue, and include `Closes #N` when the PR fully completes it.
5. Rebase on current `main` before pushing. Never force-push a shared branch.
6. Post on the board when a merge changes an interface, schema, environment variable, or another person's work.

SharkByte's event rules require the core project code, design, and assets to be created during the hackathon weekend. Open-source libraries and APIs are allowed.

## Skills

- [hosting-site-with-tailscale](.claude/skills/hosting-site-with-tailscale/SKILL.md): putting the dashboard online from your own laptop with Tailscale Funnel, including one-time setup, each run, checks and troubleshooting. Claude Code loads it automatically; other agents should read it before hosting.
