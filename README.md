# Robotic Waste Sorter — SharkByte 2026

A 3D printed robotic arm sorts a small set of demo items into labeled bins. A fixed camera sees one item in a marked pickup area. YOLO runs on a laptop, and an ESP32-S3 controls the arm and gripper. The laptop sends a bin command only when the item is recognized with enough confidence. Read [PLAN.md](PLAN.md) for the scope, build order, and demo plan.

This is a hackathon prototype, not a general recycling classifier. The servos have been tested; the arm has not yet been printed. Team ownership and assigned issues are in [PLAN.md](PLAN.md).

## Get started

1. Accept the repository invitation, then clone the repo.
2. Install [GitHub CLI](https://cli.github.com/) and run `gh auth login` with your own account.
3. In the repository, run `tools/board whoami`, then `tools/board read -n 20`.
4. Read [AGENTS.md](AGENTS.md), [PLAN.md](PLAN.md), and [kickoff issue #10](https://github.com/abhiwasabi/sharkbyte-2026/issues/10).

The pinned **Message Board** issue is for handoffs and decisions. Use `tools/board post "[Your Name's Agent] message"` to post. `tools/board unread` shows new posts and marks ones addressed to you. See `tools/board help` for all commands.

The board script uses the GitHub CLI and the repository's `origin` remote. It does not start or wake a coding agent by itself. Each teammate should check the board when starting work, before commits, and after merges.

The earlier sign-language task issues #2–#9 remain closed as superseded. The current sorter tasks are [issues #12–#17](https://github.com/abhiwasabi/sharkbyte-2026/issues). Work on one issue per branch and open a pull request when done.
