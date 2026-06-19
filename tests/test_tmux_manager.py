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


def test_agent_command_enables_network_for_room_api(tmp_path) -> None:
    auth_file = tmp_path / "auth.json"
    auth_file.write_text("{}", encoding="utf-8")
    runtime_dir = tmp_path / "runtime" / "agent"
    prompt_path = runtime_dir / "room-goal.md"
    manager = TmuxManager(Path.cwd(), tmp_path, auth_file)

    command = manager._agent_command(runtime_dir, prompt_path)

    assert "-c sandbox_workspace_write.network_access=true" in command


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
