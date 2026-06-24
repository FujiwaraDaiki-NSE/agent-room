# Agent Room

Agent Room is a round-table meeting app for multiple Codex agents.

The room log is the shared state. Each agent reads and posts through the room API. Agents are peers at the table. The controller is a special user-side operator that can guide the meeting, pass goals, and stop or deploy agent panes.

## Features

- uv-based Python app
- FastAPI room server
- Round-table GUI with agent avatars and speech bubbles
- Right-side chronological message log
- Separate private controller chat
- tmux meeting window
- Codex TUI agents in panes in the same tmux window
- Controller agent with lifecycle commands
- Controller runs with workspace write access; other agents run read-only with network access
- Multiple agent templates with personalities
- Agent teams that select template groups
- Per-template `.codex` and `AGENTS.md`
- Codex subagent config under each template

## Structure

```text
controller/
  AGENTS.md
  .codex/
  agent.json
  avatar.svg

share/
  README.md

agent-templates/
  critic/

agent-teams/
  critique-lab/
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

To show the GUI from another PC on the same network, bind the app to every interface:

```bash
uv sync
export AGENT_ROOM_SERVER_URL=http://127.0.0.1:8765
scripts/start_tmux_meeting.sh agent-room 0.0.0.0 8765 "$(pwd)/.runtime/data" "$(pwd)" "$HOME/.codex/auth.json"
```

Open this from the other PC, replacing `<server-lan-ip>` with the server machine's LAN IP:

```text
http://<server-lan-ip>:8765
```

If the browser cannot connect, allow TCP port `8765` through the server machine's firewall.
Keep `AGENT_ROOM_SERVER_URL` pointed at `127.0.0.1` when agents run on the same server machine.

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

This keeps speaking tendency and agent config isolated without forcing each pane to log in again.

## Room Flow

1. Enter `Goal`.
2. Enter `Controller Termination`.
3. Enter `Agent Termination`.
4. Select shared context directories from `share/`.
5. Select templates and teams.
6. Press `Start`.
7. The app starts only the `Controller` pane. Checked regular agents are stored as planned agents.
8. The controller checks planned agents with `planned_agents` and deploys them with `agent_deploy` only when their viewpoint is needed.
9. Each deployed Codex TUI starts with `/goal`.
10. Agents read selected shared context through `./share/<context-name>`.
11. Agents read and post through the Agent Room MCP tools.
12. The room starts quiet for regular agents, so the controller posts the first facilitation message and opens discussion.
13. The controller closes discussion, posts the final implementation handoff report, and finishes the room.
14. Finishing the room closes all agent panes. A later Controller tab message resumes the controller session when possible.

## Workshop Flow

The controller runs the room as a workshop, not open-ended free discussion.

1. `align`: align the goal, decision scope, expected output, and action format.
2. `diverge`: collect many ideas without judging them.
3. `cluster`: group similar ideas and name the main directions.
4. `deepen`: turn 2-3 promising directions into concrete candidate sheets covering target, problem, mechanism, non-goals, assumptions, failure modes, revisions, validation evidence, and stop conditions.
5. `evaluate`: compare shortlisted ideas by effect, feasibility, urgency, and cost.
6. `converge`: classify ideas into implement now, research next, and drop for now; prepare an implementation handoff report.

Agent personalities stay active, but meeting roles are temporary. The controller assigns different viewpoints to different agents each phase so one agent is not permanently fixed to one perspective.

Every phase ends with a consensus gate. The controller posts the current synthesis, explains what will change if the room advances, and asks agents for `accept`, `revise`, or `block` with reasons. A blocker must be resolved, split, or recorded as an unresolved implementation question before advancing.

The final report should be detailed enough for another agent to start implementation. It includes purpose, background, decisions, requirements, verification viewpoints, risks, and unresolved questions.

When narrowing ideas, the controller must explain the criteria, supporting evidence, selected ideas, dropped or merged ideas, accepted tradeoff, and what would change the decision later.

During `deepen`, agents should improve the precision of shortlisted ideas rather than only add opinions. Each contribution should either clarify the mechanism, expose a testable assumption, propose a concrete revision, or define a small validation step.

## Shared Context

Create one directory per shared context under the repository-level `share/` directory:

```text
share/
  product-spec/
  reference-repo/
```

The setup UI lists direct directories under `share/`.
Selected directories are exposed to deployed agents as `./share/<context-name>`.
The app copies selected directories into each agent runtime as a snapshot.
Dependency, build, and cache directories such as `node_modules`, `.venv`, `.next`, `dist`, and `tmp` are skipped.
Room start waits for these runtime copies and agent deployment to finish before the room becomes `Open`.

## Room Controls

- `New`: Stop current agent panes and replace the current room with a fresh draft room.
- `Start`: Start the current room from the form and deploy the checked templates.

`Teams` check or clear matching template checkboxes. `Default` contains the original starter set. `Critique Lab` contains perspective-specific critics and an idea revision agent. `Malcontent Table` contains complaint-heavy agents, including one sharp critic and a skeptical debater.
- `Close`: Stop the current room manually and close its agent panes.
- `Refresh`: Reload templates, the current room, messages, and tmux status.

The app keeps only one current room. Use `New` to clear the public log and controller private log by replacing the room.
The room name is internal and hidden in the GUI.
After `Start`, the top brief keeps `Goal`, `Controller Termination`, and `Agent Termination` visible.
The live room shows controller-managed meeting status, a compact participant roster, round table, bottom progress strip, and activity rail.

The `Controller` tab is private. Use it for instructions or whispers that should go only to the controller.
If the controller pane is closed, a Controller tab message resumes the saved controller Codex session when possible.

## Agent MCP Tools

Each runtime agent gets a stdio MCP server in `.codex/config.toml`.
Regular agents receive these tools:

- `room_read`
- `room_post`
- `room_done`
- `share_contexts`
- `share_list`
- `share_read`

Controller agents also receive:

- `controller_read`
- `controller_post`
- `agent_deploy`
- `planned_agents`
- `agent_stop`
- `agent_goal`
- `agent_config`
- `room_status_update`
- `room_close_discussion`
- `room_open_discussion`
- `room_finish`
- `agent_mute`
- `agent_unmute`

`agent config` writes only to the copied runtime directory for that agent. Template originals under `agent-templates/` are not modified during a meeting.
`room_done` marks only the calling agent done.
`room_status_update` updates the left-side `Meeting` panel and bottom progress strip for the user.
`room_close_discussion` stops regular agents from posting public messages while allowing controller and user messages.
`room_finish` marks the room done and closes every agent pane, including the controller pane. Use the Controller tab to resume the controller later.
`agent_mute` stops one regular agent from posting public messages without closing its pane.
Use `share_contexts`, `share_list`, and `share_read` to inspect selected shared context. Runtime snapshots are also present under `./share/`, so shell tools such as `rg` and `find` can inspect them directly.

The older CLI commands remain available for manual debugging, but deployed agents are instructed to use MCP tools instead of direct HTTP or CLI calls.

Manual MCP server run:

```bash
uv run agent-room mcp --server http://127.0.0.1:8765 --room-id <room-id> --agent-id <agent-id> --agent-name <agent-name> --share-root <runtime-share-dir>
```

## Stop

Use `Close` in the GUI to stop the room manually. The app closes agent panes only.
Controller-led workshop completion uses `room_finish`, which marks the room done and closes every agent pane.

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
  "name": "反証役",
  "shortName": "反証",
  "role": "レビュー",
  "personality": "発言傾向: 弱い前提、隠れた矛盾、都合のよい解釈を疑う。",
  "accent": "#D94841",
  "avatar": "avatar.svg",
  "summary": "弱い前提、隠れた矛盾、都合のよい解釈を疑う。",
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
