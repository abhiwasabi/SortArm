# SignSpeak — SharkByte 2026

A hackathon prototype for recognizing a small set of selected ASL signs, showing the proposed text, and speaking it after the signer confirms. Read [PLAN.md](PLAN.md) for the full MVP, architecture, UI checklist, roles, and demo plan. The team can refine the scope together at kickoff.

## Get started

1. Accept the repository invitation, then clone the repo.
2. Install [GitHub CLI](https://cli.github.com/) and run `gh auth login` with your own account.
3. In the repository, run `tools/board whoami`, then `tools/board read -n 20`.
4. Read [AGENTS.md](AGENTS.md). Agree on work areas and update its owner table.

The pinned **Message Board** issue is for handoffs and decisions. Use `tools/board post "[Your Name's Agent] message"` to post. `tools/board unread` shows new posts and marks ones addressed to you. See `tools/board help` for all commands.

The board script uses the GitHub CLI and the repository's `origin` remote. It does not start or wake a coding agent by itself. Each teammate should check the board when starting work, before commits, and after merges.

Project tasks belong in separate GitHub issues. Work on one issue per branch and open a pull request when done.
