# SharkByte 2026 team rules

## Team

- @abhiwasabi — Abhi, team lead
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

The agreed SignSpeak assignments are below. Post handoffs or scope changes on the board. The team lead resolves overlapping assignments. Ask on the board before editing another person's area. Read [PLAN.md](PLAN.md) for the MVP and interface contract.

| Area or folder | Owner | Notes |
| --- | --- | --- |
| Camera stream and firmware | @PaulPio | `firmware/camera/` |
| ESP32-S3 status device and 3D printed rig | @yusufdirdis | `firmware/status/`, `hardware/` |
| Selected-sign recognition, web app, UI, text to speech | @abhiwasabi | `recognition/`, `web/` |
| Integration, API, testing, demo and submission coordination | @anaoliveira01 | `server/`, `scripts/`, `docs/`, `PLAN.md` |

## Git workflow

1. Pick or file an issue for each task. Comment when you take it.
2. Branch from current `main` using `<issue-number>-<short-name>`.
3. Commit only your own files; stage explicit paths.
4. Open a pull request referencing the task issue, and include `Closes #N` when the PR fully completes it.
5. Rebase on current `main` before pushing. Never force-push a shared branch.
6. Post on the board when a merge changes an interface, schema, environment variable, or another person's work.

SharkByte's event rules require the core project code, design, and assets to be created during the hackathon weekend. Open-source libraries and APIs are allowed.
