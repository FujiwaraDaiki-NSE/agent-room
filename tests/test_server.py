import json
from pathlib import Path

from fastapi.testclient import TestClient

from agent_room.models import AgentInstance
from agent_room.server import create_app
from agent_room.tmux_manager import TmuxManager


def write_room_agents(data_dir: Path, room_id: str, agents: list[AgentInstance]) -> None:
    state_path = data_dir / "state.json"
    data = json.loads(state_path.read_text(encoding="utf-8"))
    data["rooms"][room_id]["agents"] = [agent.model_dump() for agent in agents]
    state_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def agent_instance(
    agent_id: str,
    template_id: str,
    state: str,
    pane_id: str | None,
) -> AgentInstance:
    return AgentInstance(
        id=agent_id,
        room_id="room-test",
        template_id=template_id,
        name=template_id.title(),
        short_name=template_id[:4].title(),
        role="role",
        personality="personality",
        accent="#136F63",
        avatar_url=f"/api/templates/{template_id}/avatar",
        state=state,  # type: ignore[arg-type]
        pane_id=pane_id,
        runtime_dir=None,
    )


def test_api_room_without_agents(tmp_path) -> None:
    auth_file = tmp_path / "auth.json"
    auth_file.write_text("{}", encoding="utf-8")
    client = TestClient(create_app(Path.cwd(), tmp_path, auth_file))

    initial_rooms = client.get("/api/rooms").json()
    assert len(initial_rooms) == 1
    assert initial_rooms[0]["state"] == "draft"

    response = client.post(
        "/api/rooms",
        json={
            "name": "Design",
            "goal": "Discuss architecture",
            "controller_termination": "Controller closes the room",
            "agent_termination": "Each agent is done",
            "templates": [],
        },
    )

    assert response.status_code == 200
    room = response.json()
    assert room["goal"] == "Discuss architecture"
    assert room["controller_termination"] == "Controller closes the room"
    assert room["agent_termination"] == "Each agent is done"
    assert room["agents"] == []

    messages = client.get(f"/api/rooms/{room['id']}/messages").json()
    assert messages[0]["kind"] == "goal"


def test_controller_private_messages_are_separate(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(TmuxManager, "send_controller_whisper", lambda *_args: None)
    auth_file = tmp_path / "auth.json"
    auth_file.write_text("{}", encoding="utf-8")
    client = TestClient(create_app(Path.cwd(), tmp_path, auth_file))
    room = client.get("/api/rooms").json()[0]
    write_room_agents(
        tmp_path,
        room["id"],
        [agent_instance("controller-1", "controller", "active", "%1")],
    )

    response = client.post(
        f"/api/rooms/{room['id']}/controller/messages",
        json={
            "actor_type": "user",
            "actor_id": "user",
            "actor_name": "User",
            "text": "Private",
            "kind": "message",
        },
    )

    assert response.status_code == 200
    assert client.get(f"/api/rooms/{room['id']}/messages").json() == []
    private_messages = client.get(f"/api/rooms/{room['id']}/controller/messages").json()
    assert [message["text"] for message in private_messages] == ["Private"]


def test_controller_private_message_notifies_controller_pane(tmp_path, monkeypatch) -> None:
    calls = []

    def fake_whisper(_manager, agent, text) -> None:
        calls.append((agent.id, agent.pane_id, text))

    monkeypatch.setattr(TmuxManager, "send_controller_whisper", fake_whisper)
    auth_file = tmp_path / "auth.json"
    auth_file.write_text("{}", encoding="utf-8")
    client = TestClient(create_app(Path.cwd(), tmp_path, auth_file))
    room = client.get("/api/rooms").json()[0]
    write_room_agents(
        tmp_path,
        room["id"],
        [agent_instance("controller-1", "controller", "active", "%1")],
    )

    response = client.post(
        f"/api/rooms/{room['id']}/controller/messages",
        json={
            "actor_type": "user",
            "actor_id": "user",
            "actor_name": "User",
            "text": "Check privately",
            "kind": "message",
        },
    )

    assert response.status_code == 200
    assert calls == [("controller-1", "%1", "Check privately")]


def test_room_done_stops_agent_panes_but_keeps_controller_pane(tmp_path, monkeypatch) -> None:
    stopped = []

    def fake_stop(_manager, agent, force) -> None:
        stopped.append((agent.id, agent.pane_id, force))

    monkeypatch.setattr(TmuxManager, "stop", fake_stop)
    auth_file = tmp_path / "auth.json"
    auth_file.write_text("{}", encoding="utf-8")
    client = TestClient(create_app(Path.cwd(), tmp_path, auth_file))
    room = client.post(
        "/api/rooms",
        json={
            "name": "Design",
            "goal": "Discuss architecture",
            "controller_termination": "Controller closes the room",
            "agent_termination": "Each agent is done",
            "templates": [],
        },
    ).json()
    write_room_agents(
        tmp_path,
        room["id"],
        [
            agent_instance("controller-1", "controller", "active", "%1"),
            agent_instance("critic-1", "critic", "active", "%2"),
            agent_instance("builder-1", "builder", "done", "%3"),
        ],
    )

    response = client.post(
        f"/api/rooms/{room['id']}/done",
        json={"actor_id": "controller-1", "reason": "criteria met"},
    )

    assert response.status_code == 200
    assert stopped == [("critic-1", "%2", False), ("builder-1", "%3", False)]
    updated = response.json()
    assert updated["state"] == "done"
    panes = {agent["id"]: agent["pane_id"] for agent in updated["agents"]}
    states = {agent["id"]: agent["state"] for agent in updated["agents"]}
    assert panes == {"controller-1": "%1", "critic-1": None, "builder-1": None}
    assert states["controller-1"] == "active"
    assert states["critic-1"] == "stopped"
    assert states["builder-1"] == "stopped"


def test_room_stop_closes_all_panes_even_when_agents_are_done(tmp_path, monkeypatch) -> None:
    stopped = []

    def fake_stop(_manager, agent, force) -> None:
        stopped.append((agent.id, agent.pane_id, force))

    monkeypatch.setattr(TmuxManager, "stop", fake_stop)
    auth_file = tmp_path / "auth.json"
    auth_file.write_text("{}", encoding="utf-8")
    client = TestClient(create_app(Path.cwd(), tmp_path, auth_file))
    room = client.post(
        "/api/rooms",
        json={
            "name": "Design",
            "goal": "Discuss architecture",
            "controller_termination": "Controller closes the room",
            "agent_termination": "Each agent is done",
            "templates": [],
        },
    ).json()
    write_room_agents(
        tmp_path,
        room["id"],
        [
            agent_instance("controller-1", "controller", "active", "%1"),
            agent_instance("critic-1", "critic", "done", "%2"),
            agent_instance("builder-1", "builder", "stopped", "%3"),
        ],
    )

    response = client.post(
        f"/api/rooms/{room['id']}/stop",
        json={"actor_id": "user", "reason": "close", "force": True},
    )

    assert response.status_code == 200
    assert stopped == [("controller-1", "%1", True), ("critic-1", "%2", True), ("builder-1", "%3", True)]
    updated = response.json()
    assert updated["state"] == "stopped"
    assert all(agent["pane_id"] is None for agent in updated["agents"])


def test_deploy_requires_tmux(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("TMUX_PANE", raising=False)
    auth_file = tmp_path / "auth.json"
    auth_file.write_text("{}", encoding="utf-8")
    client = TestClient(create_app(Path.cwd(), tmp_path, auth_file))
    room = client.post(
        "/api/rooms",
        json={
            "name": "Design",
            "goal": "Discuss architecture",
            "controller_termination": "Controller closes the room",
            "agent_termination": "Each agent is done",
            "templates": [],
        },
    ).json()

    response = client.post(
        f"/api/rooms/{room['id']}/agents",
        json={"template_id": "critic", "count": 1, "actor_id": "controller"},
    )

    assert response.status_code == 400
    assert "inside tmux" in response.json()["detail"]
