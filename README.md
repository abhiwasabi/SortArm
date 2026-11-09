# Robotic Waste Sorter — SharkByte 2026

A 3D printed robotic arm is being built to sort demo items into two labeled bins: **Plastic and Metal**. A fixed camera observes one item in a marked pickup area. The laptop handles the vision decision and controls the planned SO-101 arm over its USB servo adapter. The ESP32-S3 may serve as a physical stop/status accessory. Read [PLAN.md](PLAN.md) for the current scope, build order, and demo plan.

This is a hackathon prototype, not a general material classifier. Four-bin physical routing still needs taught arm poses and testing; hardware progress is tracked in #12 and #20. Team ownership and assigned issues are in [PLAN.md](PLAN.md).

## Get started

1. Accept the repository invitation, then clone the repo.
2. Install [GitHub CLI](https://cli.github.com/) and run `gh auth login` with your own account.
3. In the repository, run `tools/board whoami`, then `tools/board read -n 20`.
4. Read [AGENTS.md](AGENTS.md), [PLAN.md](PLAN.md), and [kickoff issue #10](https://github.com/abhiwasabi/sharkbyte-2026/issues/10).

The pinned **Message Board** issue is for handoffs and decisions. Use `tools/board post "[Your Name's Agent] message"` to post. `tools/board unread` shows new posts and marks ones addressed to you. See `tools/board help` for all commands.

The board script uses the GitHub CLI and the repository's `origin` remote. It does not start or wake a coding agent by itself. Each teammate should check the board when starting work, before commits, and after merges.

The earlier sign-language task issues #2–#9 remain closed as superseded. The current sorter tasks are tracked in [the issue board](https://github.com/abhiwasabi/sharkbyte-2026/issues), including #20 for arm control and #23 for the four-bin contract. Work on one issue per branch and open a pull request when done.
