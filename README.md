# SignSpeak — SharkByte 2026

A hackathon prototype with two limited modes: a glasses-mounted camera recognizes selected ASL signs and speaks approved text; speech is matched to supported phrases and shown as animated hands in the web app. Read [PLAN.md](PLAN.md) for the scope, architecture, and demo plan. Team roles are blank until the team assigns them.

## Get started

1. Accept the repository invitation, then clone the repo.
2. Install [GitHub CLI](https://cli.github.com/) and run `gh auth login` with your own account.
3. In the repository, run `tools/board whoami`, then `tools/board read -n 20`.
4. Read [AGENTS.md](AGENTS.md) and [PLAN.md](PLAN.md) for the open roles, interfaces, and build order.

The pinned **Message Board** issue is for handoffs and decisions. Use `tools/board post "[Your Name's Agent] message"` to post. `tools/board unread` shows new posts and marks ones addressed to you. See `tools/board help` for all commands.

The board script uses the GitHub CLI and the repository's `origin` remote. It does not start or wake a coding agent by itself. Each teammate should check the board when starting work, before commits, and after merges.

Start with the [fresh kickoff issue](https://github.com/abhiwasabi/sharkbyte-2026/issues/10) to agree on the demo and roles. The earlier task issues were closed as superseded. After kickoff, create focused GitHub issues, work on one issue per branch, and open a pull request when done.
