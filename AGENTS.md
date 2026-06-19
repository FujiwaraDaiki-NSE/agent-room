# Agent Room Project

## Implementation Policy

Keep the app small. Do not add fallback behavior for missing runtime parameters.

## Runtime Policy

- Start the meeting app inside tmux.
- The meeting app pane owns the server process.
- Deployed agents are split into panes in the same tmux window.
- Stop agents by closing only their panes.
- Do not kill the tmux session when stopping a room.

## Controller Policy

Controller instructions live in `controller/AGENTS.md`.
The controller may use lifecycle commands exposed by the app, but normal agents may not.

## Agent Template Policy

Every template must include:

- `agent.json`
- `AGENTS.md`
- `.codex/config.toml`
- `avatar.svg`

Deploy must fail when a required template file is missing.
