from pathlib import Path

from agent_room.models import AgentInstance
from agent_room.models import AgentTemplate, Room
from agent_room.tmux_manager import TmuxManager


MEETING_STATUS = {
    "phase": "Open",
    "topic": "Discuss",
    "summary": "Running",
    "decisions": [],
    "open_questions": [],
    "next": "Controller",
    "updated_at": None,
}


def test_link_shared_auth(tmp_path) -> None:
    auth_file = tmp_path / "auth.json"
    auth_file.write_text("{}", encoding="utf-8")
    runtime_dir = tmp_path / "runtime" / "agent"
    (runtime_dir / ".codex").mkdir(parents=True)

    manager = TmuxManager(Path.cwd(), tmp_path, auth_file)
    manager._link_shared_auth(runtime_dir)

    linked = runtime_dir / ".codex" / "auth.json"
    assert linked.is_symlink()
    assert linked.resolve() == auth_file


def test_copy_share_contexts_copies_selected_directories(tmp_path) -> None:
    project_root = tmp_path / "project"
    source = project_root / "share" / "alpha"
    source.mkdir(parents=True)
    (source / "README.md").write_text("Alpha context\n", encoding="utf-8")
    (source / "node_modules").mkdir()
    (source / "node_modules" / "dependency.js").write_text("ignored\n", encoding="utf-8")
    (source / ".venv").mkdir()
    (source / ".venv" / "python").write_text("ignored\n", encoding="utf-8")
    (source / "src").mkdir()
    (source / "src" / "main.py").write_text("print('alpha')\n", encoding="utf-8")
    auth_file = tmp_path / "auth.json"
    auth_file.write_text("{}", encoding="utf-8")
    runtime_dir = tmp_path / "runtime" / "agent"
    runtime_dir.mkdir(parents=True)
    manager = TmuxManager(project_root, tmp_path, auth_file)

    manager._copy_share_contexts(runtime_dir, ["alpha"])

    copied = runtime_dir / "share" / "alpha"
    assert copied.is_dir()
    assert not copied.is_symlink()
    assert (copied / "README.md").read_text(encoding="utf-8") == "Alpha context\n"
    assert (copied / "src" / "main.py").is_file()
    assert not (copied / "node_modules").exists()
    assert not (copied / ".venv").exists()


def test_trust_project_adds_runtime_trust_once(tmp_path) -> None:
    auth_file = tmp_path / "auth.json"
    auth_file.write_text("{}", encoding="utf-8")
    runtime_dir = tmp_path / "runtime" / "agent"
    (runtime_dir / ".codex").mkdir(parents=True)
    config_path = runtime_dir / ".codex" / "config.toml"
    config_path.write_text("[features]\ngoals = true\n", encoding="utf-8")
    manager = TmuxManager(Path("/home/solution2024/agent-room"), tmp_path, auth_file)

    manager._trust_project(runtime_dir)
    manager._trust_project(runtime_dir)

    text = config_path.read_text(encoding="utf-8")
    assert text.count('[projects."/home/solution2024/agent-room"]') == 1
    assert 'trust_level = "trusted"' in text


def test_configure_mcp_adds_controller_tools(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_ROOM_SERVER_URL", "http://127.0.0.1:8765")
    auth_file = tmp_path / "auth.json"
    auth_file.write_text("{}", encoding="utf-8")
    runtime_dir = tmp_path / "runtime" / "agent"
    (runtime_dir / ".codex").mkdir(parents=True)
    config_path = runtime_dir / ".codex" / "config.toml"
    config_path.write_text("[features]\ngoals = true\n", encoding="utf-8")
    manager = TmuxManager(Path("/home/solution2024/agent-room"), tmp_path, auth_file)
    room = Room(
        id="room-test",
        name="Room",
        goal="Discuss",
        controller_termination="Controller done",
        agent_termination="Agents done",
        share_contexts=[],
        planned_template_ids=[],
        agent_posting_closed=False,
        muted_agent_ids=[],
        state="open",
        created_at="2026-06-19T00:00:00+00:00",
        meeting_status=MEETING_STATUS,
        agents=[],
    )
    controller = AgentTemplate.model_validate(
        {
            "id": "controller",
            "name": "Controller",
            "shortName": "Control",
            "role": "control",
            "personality": "Direct",
            "accent": "#136F63",
            "avatar": "avatar.svg",
            "scope": "controller",
            "summary": "Controls the meeting",
            "launch": True,
            "permissions": [],
        }
    )

    manager._configure_mcp(runtime_dir, room, controller, "controller-1")

    text = config_path.read_text(encoding="utf-8")
    assert "[mcp_servers.agent_room]" in text
    assert 'command = "uv"' in text
    assert '"agent-room", "mcp"' in text
    assert '"--share-root"' in text
    assert '"--controller"' in text
    assert '"controller_read"' in text
    assert '"agent_config"' in text
    assert '"planned_agents"' in text
    assert '"room_status_update"' in text
    assert '"room_close_discussion"' in text
    assert '"room_finish"' in text
    assert '"agent_mute"' in text


def test_configure_mcp_limits_regular_agent_tools(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_ROOM_SERVER_URL", "http://127.0.0.1:8765")
    auth_file = tmp_path / "auth.json"
    auth_file.write_text("{}", encoding="utf-8")
    runtime_dir = tmp_path / "runtime" / "agent"
    (runtime_dir / ".codex").mkdir(parents=True)
    config_path = runtime_dir / ".codex" / "config.toml"
    config_path.write_text("[features]\ngoals = true\n", encoding="utf-8")
    manager = TmuxManager(Path("/home/solution2024/agent-room"), tmp_path, auth_file)
    room = Room(
        id="room-test",
        name="Room",
        goal="Discuss",
        controller_termination="Controller done",
        agent_termination="Agents done",
        share_contexts=[],
        planned_template_ids=[],
        agent_posting_closed=False,
        muted_agent_ids=[],
        state="open",
        created_at="2026-06-19T00:00:00+00:00",
        meeting_status=MEETING_STATUS,
        agents=[],
    )
    agent = AgentTemplate.model_validate(
        {
            "id": "critic",
            "name": "Critic",
            "shortName": "Critic",
            "role": "review",
            "personality": "Skeptical",
            "accent": "#D94841",
            "avatar": "avatar.svg",
            "scope": "agent",
            "summary": "Reviews",
            "launch": True,
            "permissions": [],
        }
    )

    manager._configure_mcp(runtime_dir, room, agent, "critic-1")

    text = config_path.read_text(encoding="utf-8")
    assert '"room_read", "room_post", "room_done", "share_contexts", "share_list", "share_read"' in text
    assert '"--share-root"' in text
    assert '"--controller"' not in text
    assert '"controller_read"' not in text
    assert '"agent_config"' not in text
    assert '"planned_agents"' not in text
    assert '"room_status_update"' not in text
    assert '"room_close_discussion"' not in text
    assert '"room_finish"' not in text
    assert '"agent_mute"' not in text


def test_controller_command_enables_network_and_workspace_write(tmp_path) -> None:
    auth_file = tmp_path / "auth.json"
    auth_file.write_text("{}", encoding="utf-8")
    runtime_dir = tmp_path / "runtime" / "agent"
    prompt_path = runtime_dir / "room-goal.md"
    manager = TmuxManager(Path.cwd(), tmp_path, auth_file)

    command = manager._agent_command(runtime_dir, prompt_path, can_write=True)

    assert "--sandbox workspace-write" in command
    assert "-c sandbox_workspace_write.network_access=true" in command


def test_agent_command_enables_network_without_workspace_write(tmp_path) -> None:
    auth_file = tmp_path / "auth.json"
    auth_file.write_text("{}", encoding="utf-8")
    runtime_dir = tmp_path / "runtime" / "agent"
    prompt_path = runtime_dir / "room-goal.md"
    manager = TmuxManager(Path.cwd(), tmp_path, auth_file)

    command = manager._agent_command(runtime_dir, prompt_path, can_write=False)

    assert "--sandbox workspace-write" not in command
    assert '-c \'default_permissions="agent_read_network"\'' in command
    assert '-c \'permissions.agent_read_network.extends=":read-only"\'' in command
    assert "-c permissions.agent_read_network.network.enabled=true" in command


def test_goal_prompt_splits_controller_and_agent_termination(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_ROOM_SERVER_URL", "http://127.0.0.1:8765")
    auth_file = tmp_path / "auth.json"
    auth_file.write_text("{}", encoding="utf-8")
    manager = TmuxManager(Path.cwd(), tmp_path, auth_file)
    room = Room(
        id="room-test",
        name="Room",
        goal="Discuss",
        controller_termination="Controller done",
        agent_termination="Agents done",
        share_contexts=["alpha"],
        planned_template_ids=["critic", "researcher"],
        agent_posting_closed=False,
        muted_agent_ids=[],
        state="open",
        created_at="2026-06-19T00:00:00+00:00",
        meeting_status=MEETING_STATUS,
        agents=[],
    )
    controller = AgentTemplate.model_validate(
        {
            "id": "controller",
            "name": "Controller",
            "shortName": "Control",
            "role": "control",
            "personality": "Direct",
            "accent": "#136F63",
            "avatar": "avatar.svg",
            "scope": "controller",
            "summary": "Controls the meeting",
            "launch": True,
            "permissions": [],
        }
    )
    agent = AgentTemplate.model_validate(
        {
            "id": "critic",
            "name": "Critic",
            "shortName": "Critic",
            "role": "review",
            "personality": "Skeptical",
            "accent": "#D94841",
            "avatar": "avatar.svg",
            "scope": "agent",
            "summary": "Reviews",
            "launch": True,
            "permissions": [],
        }
    )

    controller_prompt = manager._goal_prompt(
        room,
        controller,
        "controller-1",
        "Discuss",
        "Controller done",
        "Agents done",
    )
    agent_prompt = manager._goal_prompt(
        room,
        agent,
        "critic-1",
        "Discuss",
        "Controller done",
        "Agents done",
    )

    assert "Controller Termination:\nController done" in controller_prompt
    assert "Agent Termination:\nAgents done" in controller_prompt
    assert "./share/alpha" in controller_prompt
    assert "copied runtime snapshots" in controller_prompt
    assert "Controller Termination:" not in agent_prompt
    assert "Agent Termination:\nAgents done" in agent_prompt
    assert "./share/alpha" in agent_prompt
    assert "copied runtime snapshots" in agent_prompt
    assert "Agent Room MCP tools" in controller_prompt
    assert "share_contexts" in controller_prompt
    assert "share_list" in controller_prompt
    assert "share_read" in controller_prompt
    assert "Room communication rules:" in controller_prompt
    assert "`宛先: 全体` or `宛先: <相手名>`" in controller_prompt
    assert "Do not post unexplained fragments" in controller_prompt
    assert "room_status_update" in controller_prompt
    assert "planned_agents" in controller_prompt
    assert "room_close_discussion" in controller_prompt
    assert "room_finish" in controller_prompt
    assert "agent_mute" in controller_prompt
    assert "The room starts quiet for regular agents" in controller_prompt
    assert "state whether the reply is for the whole room or for a named person" in controller_prompt
    assert "room_open_discussion" in controller_prompt
    assert "Planned Agents:" in controller_prompt
    assert "- critic" in controller_prompt
    assert "- researcher" in controller_prompt
    assert "uv run agent-room room" not in controller_prompt
    assert "room_close_discussion" not in agent_prompt
    assert "room_finish" not in agent_prompt
    assert "agent_mute" not in agent_prompt
    assert "Do not post before the controller's first public facilitation message" in agent_prompt
    assert "Planned Agents:" not in agent_prompt
    assert "share_contexts" in agent_prompt
    assert "share_list" in agent_prompt
    assert "share_read" in agent_prompt
    assert "Room communication rules:" in agent_prompt
    assert "`宛先: 全体` or `宛先: <相手名>`" in agent_prompt
    assert "Do not post unexplained fragments" in agent_prompt
    assert "room_status_update" not in agent_prompt
    assert "uv run agent-room room" not in agent_prompt


def test_send_controller_whisper_marks_private_prompt(tmp_path, monkeypatch) -> None:
    calls = []
    loaded_prompt = []
    temp_paths = []

    def fake_run(command, check, **_kwargs):
        if command[:3] == ["tmux", "load-buffer", "-b"]:
            temp_path = Path(command[4])
            temp_paths.append(temp_path)
            loaded_prompt.append(temp_path.read_text(encoding="utf-8"))
        calls.append((command, check))

    monkeypatch.setattr("subprocess.run", fake_run)
    auth_file = tmp_path / "auth.json"
    auth_file.write_text("{}", encoding="utf-8")
    manager = TmuxManager(Path.cwd(), tmp_path, auth_file)
    agent = AgentInstance(
        id="controller-1",
        room_id="room-test",
        template_id="controller",
        name="Controller",
        short_name="Control",
        role="control",
        personality="Direct",
        accent="#136F63",
        avatar_url="/api/templates/controller/avatar",
        state="active",
        pane_id="%1",
    )

    manager.send_controller_whisper(agent, "Private instruction")

    assert loaded_prompt
    assert "Private controller whisper" in loaded_prompt[0]
    assert "Private instruction" in loaded_prompt[0]
    assert "not a public room message" in loaded_prompt[0]
    load_command, load_check = calls[0]
    buffer_name = load_command[3]
    assert load_command[:3] == ["tmux", "load-buffer", "-b"]
    assert buffer_name.startswith("agent-room-")
    assert temp_paths and not temp_paths[0].exists()
    assert load_check is True
    assert calls[1] == (["tmux", "paste-buffer", "-dpr", "-b", buffer_name, "-t", "%1"], True)
    assert calls[2] == (["tmux", "send-keys", "-t", "%1", "Enter"], True)


def test_send_goal_includes_room_communication_rules(tmp_path, monkeypatch) -> None:
    sent = []
    auth_file = tmp_path / "auth.json"
    auth_file.write_text("{}", encoding="utf-8")
    manager = TmuxManager(Path.cwd(), tmp_path, auth_file)
    agent = AgentInstance(
        id="critic-1",
        room_id="room-test",
        template_id="critic",
        name="Critic",
        short_name="Critic",
        role="review",
        personality="Skeptical",
        accent="#D94841",
        avatar_url="/api/templates/critic/avatar",
        state="active",
        pane_id="%2",
    )

    monkeypatch.setattr(manager, "_send_codex_prompt", lambda pane_id, text: sent.append((pane_id, text)))

    manager.send_goal(agent, "Discuss next", "Controller done", "Agents done")

    assert sent
    pane_id, prompt = sent[0]
    assert pane_id == "%2"
    assert "Goal:\nDiscuss next" in prompt
    assert "Room communication rules:" in prompt
    assert "`宛先: 全体` or `宛先: <相手名>`" in prompt
    assert "Do not post unexplained fragments" in prompt


def test_resume_controller_uses_saved_codex_session(tmp_path, monkeypatch) -> None:
    session_id = "11111111-2222-3333-4444-555555555555"
    auth_file = tmp_path / "auth.json"
    auth_file.write_text("{}", encoding="utf-8")
    runtime_dir = tmp_path / "runtime" / "controller"
    sessions_dir = runtime_dir / ".codex" / "sessions"
    sessions_dir.mkdir(parents=True)
    (sessions_dir / f"rollout-2026-06-22-{session_id}.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("TMUX_PANE", "%0")
    manager = TmuxManager(Path.cwd(), tmp_path, auth_file)
    commands = []

    def fake_split(command: str) -> str:
        commands.append(command)
        return "%9"

    monkeypatch.setattr(manager, "_split_pane", fake_split)
    agent = AgentInstance(
        id="controller-1",
        room_id="room-test",
        template_id="controller",
        name="Controller",
        short_name="Control",
        role="control",
        personality="Direct",
        accent="#136F63",
        avatar_url="/api/templates/controller/avatar",
        state="stopped",
        pane_id=None,
        runtime_dir=str(runtime_dir),
    )

    pane_id, resolved_session_id = manager.resume_controller(agent, "Follow up")

    assert pane_id == "%9"
    assert resolved_session_id == session_id
    assert commands
    assert "codex resume --sandbox workspace-write --ask-for-approval never" in commands[0]
    assert session_id in commands[0]
    assert "controller-resume-prompt.md" in commands[0]
    assert "agent-exited" in commands[0]
    prompt = (runtime_dir / "controller-resume-prompt.md").read_text(encoding="utf-8")
    assert "Private controller whisper" in prompt
    assert "Follow up" in prompt


def test_resolve_codex_session_id_rejects_ambiguous_sessions(tmp_path) -> None:
    auth_file = tmp_path / "auth.json"
    auth_file.write_text("{}", encoding="utf-8")
    runtime_dir = tmp_path / "runtime" / "controller"
    sessions_dir = runtime_dir / ".codex" / "sessions"
    sessions_dir.mkdir(parents=True)
    (sessions_dir / "rollout-2026-06-22-11111111-2222-3333-4444-555555555555.json").write_text(
        "{}",
        encoding="utf-8",
    )
    (sessions_dir / "rollout-2026-06-22-66666666-7777-8888-9999-aaaaaaaaaaaa.json").write_text(
        "{}",
        encoding="utf-8",
    )
    manager = TmuxManager(Path.cwd(), tmp_path, auth_file)
    agent = AgentInstance(
        id="controller-1",
        room_id="room-test",
        template_id="controller",
        name="Controller",
        short_name="Control",
        role="control",
        personality="Direct",
        accent="#136F63",
        avatar_url="/api/templates/controller/avatar",
        state="stopped",
        pane_id=None,
        runtime_dir=str(runtime_dir),
    )

    try:
        manager.resolve_codex_session_id(agent)
    except Exception as exc:
        assert "ambiguous" in str(exc)
    else:
        raise AssertionError("expected ambiguous session error")
