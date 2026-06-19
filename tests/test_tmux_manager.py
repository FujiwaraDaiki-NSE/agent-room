from pathlib import Path

from agent_room.models import AgentTemplate, Room
from agent_room.tmux_manager import TmuxManager


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
        state="open",
        created_at="2026-06-19T00:00:00+00:00",
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
    assert '"--controller"' in text
    assert '"controller_read"' in text
    assert '"agent_config"' in text


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
        state="open",
        created_at="2026-06-19T00:00:00+00:00",
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
    assert '"room_read", "room_post", "room_done"' in text
    assert '"--controller"' not in text
    assert '"controller_read"' not in text
    assert '"agent_config"' not in text


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
        state="open",
        created_at="2026-06-19T00:00:00+00:00",
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
    assert "Controller Termination:" not in agent_prompt
    assert "Agent Termination:\nAgents done" in agent_prompt
    assert "Agent Room MCP tools" in controller_prompt
    assert "uv run agent-room room" not in controller_prompt
    assert "uv run agent-room room" not in agent_prompt
