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
            "share_contexts": [],
            "templates": [],
        },
    )

    assert response.status_code == 200
    room = response.json()
    assert room["goal"] == "Discuss architecture"
    assert room["controller_termination"] == "Controller closes the room"
    assert room["agent_termination"] == "Each agent is done"
    assert room["share_contexts"] == []
    assert room["agent_posting_closed"] is False
    assert room["muted_agent_ids"] == []
    assert room["state"] == "open"
    assert room["agents"] == []

    messages = client.get(f"/api/rooms/{room['id']}/messages").json()
    assert messages[0]["kind"] == "goal"


def test_room_opens_after_agent_deploy_finishes(tmp_path, monkeypatch) -> None:
    deploy_room_states = []
    auth_file = tmp_path / "auth.json"
    auth_file.write_text("{}", encoding="utf-8")

    def fake_deploy(self, room, template, template_dir, actor_id, goal, controller_termination, agent_termination):
        deploy_room_states.append(room.state)
        return AgentInstance(
            id=f"{template.id}-fake",
            room_id=room.id,
            template_id=template.id,
            name=template.name,
            short_name=template.short_name,
            role=template.role,
            personality=template.personality,
            accent=template.accent,
            avatar_url=f"/api/templates/{template.id}/avatar",
            state="active",
            pane_id="%1",
            runtime_dir=str(tmp_path / "runtime" / template.id),
            goal=goal,
            controller_termination=controller_termination if template.scope == "controller" else None,
            agent_termination=agent_termination,
        )

    monkeypatch.setattr(TmuxManager, "deploy", fake_deploy)
    client = TestClient(create_app(Path.cwd(), tmp_path, auth_file))

    response = client.post(
        "/api/rooms",
        json={
            "name": "Design",
            "goal": "Discuss architecture",
            "controller_termination": "Controller closes the room",
            "agent_termination": "Each agent is done",
            "share_contexts": [],
            "templates": ["controller"],
        },
    )

    assert response.status_code == 200
    room = response.json()
    assert deploy_room_states == ["starting"]
    assert room["state"] == "open"
    event_types = [event["type"] for event in client.get(f"/api/rooms/{room['id']}/events").json()]
    assert event_types == ["room.starting", "message.created", "agent.deployed", "room.started"]


def test_share_contexts_list_direct_share_directories(tmp_path) -> None:
    project_root = tmp_path / "project"
    share_root = project_root / "share"
    (share_root / "alpha").mkdir(parents=True)
    (share_root / "beta").mkdir()
    (share_root / ".hidden").mkdir()
    (share_root / "alpha" / "nested").mkdir()
    (share_root / "notes.txt").write_text("note", encoding="utf-8")
    auth_file = tmp_path / "auth.json"
    auth_file.write_text("{}", encoding="utf-8")
    client = TestClient(create_app(project_root, tmp_path / "data", auth_file))

    response = client.get("/api/share/contexts")

    assert response.status_code == 200
    assert [context["name"] for context in response.json()] == ["alpha", "beta"]


def test_room_stores_selected_share_contexts(tmp_path) -> None:
    project_root = tmp_path / "project"
    (project_root / "share" / "alpha").mkdir(parents=True)
    auth_file = tmp_path / "auth.json"
    auth_file.write_text("{}", encoding="utf-8")
    client = TestClient(create_app(project_root, tmp_path / "data", auth_file))

    response = client.post(
        "/api/rooms",
        json={
            "name": "Design",
            "goal": "Discuss architecture",
            "controller_termination": "Controller closes the room",
            "agent_termination": "Each agent is done",
            "share_contexts": ["alpha"],
            "templates": [],
        },
    )

    assert response.status_code == 200
    assert response.json()["share_contexts"] == ["alpha"]


def test_room_rejects_unknown_share_context(tmp_path) -> None:
    project_root = tmp_path / "project"
    (project_root / "share" / "alpha").mkdir(parents=True)
    auth_file = tmp_path / "auth.json"
    auth_file.write_text("{}", encoding="utf-8")
    client = TestClient(create_app(project_root, tmp_path / "data", auth_file))

    response = client.post(
        "/api/rooms",
        json={
            "name": "Design",
            "goal": "Discuss architecture",
            "controller_termination": "Controller closes the room",
            "agent_termination": "Each agent is done",
            "share_contexts": ["missing"],
            "templates": [],
        },
    )

    assert response.status_code == 400
    assert "share context not found: missing" in response.json()["detail"]


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


def test_closed_discussion_blocks_regular_agent_posts_but_allows_controller_and_user(tmp_path) -> None:
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
            "share_contexts": [],
            "templates": [],
        },
    ).json()
    write_room_agents(
        tmp_path,
        room["id"],
        [
            agent_instance("controller-1", "controller", "active", "%1"),
            agent_instance("critic-1", "critic", "active", "%2"),
        ],
    )

    close_response = client.post(
        f"/api/rooms/{room['id']}/discussion/close",
        json={"actor_id": "controller-1", "reason": "final summary"},
    )
    assert close_response.status_code == 200
    assert close_response.json()["agent_posting_closed"] is True

    agent_response = client.post(
        f"/api/rooms/{room['id']}/messages",
        json={
            "actor_type": "agent",
            "actor_id": "critic-1",
            "actor_name": "Critic",
            "text": "One more concern",
            "kind": "message",
        },
    )
    assert agent_response.status_code == 400
    assert "room discussion is closed" in agent_response.json()["detail"]

    controller_response = client.post(
        f"/api/rooms/{room['id']}/messages",
        json={
            "actor_type": "controller",
            "actor_id": "controller-1",
            "actor_name": "Controller",
            "text": "Final summary",
            "kind": "message",
        },
    )
    user_response = client.post(
        f"/api/rooms/{room['id']}/messages",
        json={
            "actor_type": "user",
            "actor_id": "user",
            "actor_name": "User",
            "text": "Acknowledged",
            "kind": "message",
        },
    )

    assert controller_response.status_code == 200
    assert user_response.status_code == 200


def test_agent_mute_blocks_only_target_agent(tmp_path) -> None:
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
            "share_contexts": [],
            "templates": [],
        },
    ).json()
    write_room_agents(
        tmp_path,
        room["id"],
        [
            agent_instance("controller-1", "controller", "active", "%1"),
            agent_instance("critic-1", "critic", "active", "%2"),
            agent_instance("builder-1", "builder", "active", "%3"),
        ],
    )

    mute_response = client.post(
        f"/api/rooms/{room['id']}/agents/critic-1/mute",
        json={"actor_id": "controller-1", "reason": "over limit"},
    )
    assert mute_response.status_code == 200
    assert mute_response.json()["muted_agent_ids"] == ["critic-1"]

    blocked = client.post(
        f"/api/rooms/{room['id']}/messages",
        json={
            "actor_type": "agent",
            "actor_id": "critic-1",
            "actor_name": "Critic",
            "text": "More",
            "kind": "message",
        },
    )
    allowed = client.post(
        f"/api/rooms/{room['id']}/messages",
        json={
            "actor_type": "agent",
            "actor_id": "builder-1",
            "actor_name": "Builder",
            "text": "Current status",
            "kind": "message",
        },
    )

    assert blocked.status_code == 400
    assert "agent is muted: critic-1" in blocked.json()["detail"]
    assert allowed.status_code == 200

    unmute_response = client.post(
        f"/api/rooms/{room['id']}/agents/critic-1/unmute",
        json={"actor_id": "controller-1", "reason": "resume"},
    )
    assert unmute_response.status_code == 200
    assert unmute_response.json()["muted_agent_ids"] == []

    resumed = client.post(
        f"/api/rooms/{room['id']}/messages",
        json={
            "actor_type": "agent",
            "actor_id": "critic-1",
            "actor_name": "Critic",
            "text": "Back",
            "kind": "message",
        },
    )
    assert resumed.status_code == 200


def test_controller_cannot_be_muted(tmp_path) -> None:
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
        f"/api/rooms/{room['id']}/agents/controller-1/mute",
        json={"actor_id": "controller-1", "reason": "bad request"},
    )

    assert response.status_code == 400
    assert "controller cannot be muted" in response.json()["detail"]


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
            "share_contexts": [],
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
            "share_contexts": [],
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
            "share_contexts": [],
            "templates": [],
        },
    ).json()

    response = client.post(
        f"/api/rooms/{room['id']}/agents",
        json={"template_id": "critic", "count": 1, "actor_id": "controller"},
    )

    assert response.status_code == 400
    assert "inside tmux" in response.json()["detail"]
