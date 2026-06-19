# Agent Room

Agent Room is a round-table meeting app for multiple Codex agents.

The room log is the shared state. Each agent reads and posts through the room API. Agents are peers at the table. The controller is a special user-side operator that can guide the meeting, pass goals, and stop or deploy agent panes.

## Features

- uv-based Python app
- FastAPI room server
- Round-table GUI with agent avatars and speech bubbles
- Right-side chronological message log
- tmux meeting window
- Codex TUI agents in panes in the same tmux window
- Controller agent with lifecycle commands
- Multiple agent templates with personalities
- Per-template `.codex` and `AGENTS.md`
- Codex subagent config under each template

## Structure

```text
controller/
  AGENTS.md
  .codex/
  agent.json
  avatar.svg

agent-templates/
  critic/
  researcher/
  builder/
  synthesizer/
  facilitator/
  operator/

src/agent_room/
  server.py
  tmux_manager.py
  static/

runtime/
  rooms/
```

## Start

Run the meeting app inside tmux. This tmux session is for the meeting app. Agent panes are created in the same tmux window by the app.

```bash
uv sync
scripts/start_tmux_meeting.sh agent-room 127.0.0.1 8765 "$(pwd)/.runtime/data" "$(pwd)" "$HOME/.codex/auth.json"
```

Open the GUI:

```text
http://127.0.0.1:8765
```

Attach to the meeting window:

```bash
tmux attach -t agent-room
```

## Authentication

Agent templates keep separate `.codex/config.toml` files, but they share the Codex login token through `--codex-auth-file`.

The app links each runtime agent's `.codex/auth.json` to the file you pass:

```bash
--codex-auth-file "$HOME/.codex/auth.json"
```

This keeps personality and agent config isolated without forcing each pane to log in again.

## Room Flow

1. Enter `Goal`.
2. Enter `Termination`.
3. Select templates.
4. Press `Start`.
5. The app creates one tmux pane per selected agent.
6. Each Codex TUI starts with `/goal`.
7. Agents read and post through the room commands.
8. The controller marks the room done or stops agent panes.

## Agent Commands

Agents receive commands in their `/goal` prompt. The command shape is:

```bash
uv run agent-room room read --server http://127.0.0.1:8765 --room-id <room-id>
uv run agent-room room post --server http://127.0.0.1:8765 --room-id <room-id> --agent-id <agent-id> --agent-name <agent-name> --text "<message>"
uv run agent-room room done --server http://127.0.0.1:8765 --room-id <room-id> --agent-id <agent-id> --reason "<reason>"
```

Controller-only lifecycle commands:

```bash
uv run agent-room agent deploy --server http://127.0.0.1:8765 --room-id <room-id> --template-id <template-id> --count 1 --actor-id <controller-id>
uv run agent-room agent stop --server http://127.0.0.1:8765 --room-id <room-id> --agent-id <target-agent-id> --actor-id <controller-id> --reason "<reason>" --graceful
uv run agent-room agent goal --server http://127.0.0.1:8765 --room-id <room-id> --agent-id <target-agent-id> --actor-id <controller-id> --goal "<goal>" --termination "<termination>"
uv run agent-room agent config --server http://127.0.0.1:8765 --room-id <room-id> --agent-id <target-agent-id> --actor-id <controller-id> --relative-path ".codex/config.toml" --content-file ./new-config.toml --reason "<reason>"
```

`agent config` writes only to the copied runtime directory for that agent. Template originals under `agent-templates/` are not modified during a meeting.

## Stop

Use `Stop` in the GUI to stop the room. The app closes agent panes only.

Manual pane close:

```bash
tmux kill-pane -t <pane-id>
```

Do not use `tmux kill-session` for normal room stop. That would close the meeting app itself.

## Templates

Each template has a manifest:

```json
{
  "id": "critic",
  "name": "Critic",
  "shortName": "Critic",
  "role": "review",
  "personality": "Skeptical, precise, and evidence-driven. Finds weak assumptions.",
  "accent": "#D94841",
  "avatar": "avatar.svg",
  "summary": "Challenges proposals, checks risks, and looks for missing tests or edge cases.",
  "launch": true,
  "permissions": ["read_room", "post_message", "mark_done"]
}
```

`avatar` is required. Missing avatar files make template loading fail.

## Development

Run tests:

```bash
uv run pytest
```

Run the server without tmux for API and GUI work:

```bash
uv run agent-room serve --host 127.0.0.1 --port 8765 --data-dir "$(pwd)/.runtime/data" --project-root "$(pwd)" --codex-auth-file "$HOME/.codex/auth.json"
```

Agent deployment requires the server to run inside tmux.
