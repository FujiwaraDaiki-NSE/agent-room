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
    runtime_dir: str | None = None,
    codex_session_id: str | None = None,
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
        runtime_dir=runtime_dir,
        codex_session_id=codex_session_id,
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
            "teams": [],
        },
    )

    assert response.status_code == 200
    room = response.json()
    assert room["goal"] == "Discuss architecture"
    assert room["controller_termination"] == "Controller closes the room"
    assert room["agent_termination"] == "Each agent is done"
    assert room["share_contexts"] == []
    assert room["planned_template_ids"] == []
    assert room["agent_posting_closed"] is True
    assert room["muted_agent_ids"] == []
    assert room["state"] == "open"
    assert room["meeting_status"]["phase"] == "Open"
    assert room["meeting_status"]["topic"] == "Discuss architecture"
    assert room["meeting_status"]["next"] == "Controller"
    assert room["agents"] == []

    messages = client.get(f"/api/rooms/{room['id']}/messages").json()
    assert messages[0]["kind"] == "goal"


def test_api_lists_agent_teams(tmp_path) -> None:
    auth_file = tmp_path / "auth.json"
    auth_file.write_text("{}", encoding="utf-8")
    client = TestClient(create_app(Path.cwd(), tmp_path, auth_file))

    response = client.get("/api/teams")

    assert response.status_code == 200
    teams = response.json()
    assert teams[0]["id"] == "default"
    assert teams[0]["templates"] == ["critic", "researcher", "builder", "synthesizer", "facilitator", "operator"]
    critique_lab = next(team for team in teams if team["id"] == "critique-lab")
    assert critique_lab["templates"] == [
        "critique-technical",
        "critique-user",
        "critique-business",
        "critique-risk",
        "idea-reviser",
    ]


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
            "teams": [],
        },
    )

    assert response.status_code == 200
    room = response.json()
    assert deploy_room_states == ["starting"]
    assert room["state"] == "open"
    event_types = [event["type"] for event in client.get(f"/api/rooms/{room['id']}/events").json()]
    assert event_types == ["room.starting", "message.created", "agent.deployed", "room.started"]


def test_room_start_stores_planned_team_templates_but_deploys_controller_only(tmp_path, monkeypatch) -> None:
    deployed = []
    auth_file = tmp_path / "auth.json"
    auth_file.write_text("{}", encoding="utf-8")

    def fake_deploy(self, room, template, template_dir, actor_id, goal, controller_termination, agent_termination):
        deployed.append(template.id)
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
            pane_id=f"%{len(deployed)}",
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
            "teams": ["critique-lab"],
        },
    )

    assert response.status_code == 200
    room = response.json()
    assert deployed == ["controller"]
    assert room["planned_template_ids"] == [
        "critique-technical",
        "critique-user",
        "critique-business",
        "critique-risk",
        "idea-reviser",
    ]
    assert [agent["template_id"] for agent in room["agents"]] == ["controller"]


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
            "teams": [],
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
            "teams": [],
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


def test_controller_updates_meeting_status(tmp_path) -> None:
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
            "teams": [],
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

    response = client.put(
        f"/api/rooms/{room['id']}/status",
        json={
            "actor_id": "controller-1",
            "phase": "Synthesis",
            "topic": "Evidence registry",
            "summary": "The discussion is converging on traceability.",
            "decisions": ["Use an evidence registry"],
            "open_questions": ["Owner granularity"],
            "next": "Ask for final objections",
        },
    )

    assert response.status_code == 200
    status = response.json()["meeting_status"]
    assert status["phase"] == "Synthesis"
    assert status["topic"] == "Evidence registry"
    assert status["summary"] == "The discussion is converging on traceability."
    assert status["decisions"] == ["Use an evidence registry"]
    assert status["open_questions"] == ["Owner granularity"]
    assert status["next"] == "Ask for final objections"
    assert status["updated_at"]
    events = client.get(f"/api/rooms/{room['id']}/events").json()
    assert events[-1]["type"] == "room.status_updated"


def test_regular_agent_cannot_update_meeting_status(tmp_path) -> None:
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
            "teams": [],
        },
    ).json()
    write_room_agents(tmp_path, room["id"], [agent_instance("critic-1", "critic", "active", "%2")])

    response = client.put(
        f"/api/rooms/{room['id']}/status",
        json={
            "actor_id": "critic-1",
            "phase": "Synthesis",
            "topic": "Evidence registry",
            "summary": "The discussion is converging.",
            "decisions": [],
            "open_questions": [],
            "next": "Continue",
        },
    )

    assert response.status_code == 400
    assert "meeting status can only be updated by controller" in response.json()["detail"]


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


def test_controller_private_message_resumes_stopped_controller(tmp_path, monkeypatch) -> None:
    calls = []

    def fake_resume(_manager, agent, text):
        calls.append((agent.id, agent.pane_id, text))
        return "%9", "11111111-2222-3333-4444-555555555555"

    monkeypatch.setattr(TmuxManager, "resume_controller", fake_resume)
    auth_file = tmp_path / "auth.json"
    auth_file.write_text("{}", encoding="utf-8")
    client = TestClient(create_app(Path.cwd(), tmp_path, auth_file))
    room = client.get("/api/rooms").json()[0]
    write_room_agents(
        tmp_path,
        room["id"],
        [agent_instance("controller-1", "controller", "stopped", None, str(tmp_path / "runtime" / "controller"))],
    )

    response = client.post(
        f"/api/rooms/{room['id']}/controller/messages",
        json={
            "actor_type": "user",
            "actor_id": "user",
            "actor_name": "User",
            "text": "Follow up",
            "kind": "message",
        },
    )

    assert response.status_code == 200
    assert calls == [("controller-1", None, "Follow up")]
    updated = client.get(f"/api/rooms/{room['id']}").json()
    controller = updated["agents"][0]
    assert controller["state"] == "active"
    assert controller["pane_id"] == "%9"
    assert controller["codex_session_id"] == "11111111-2222-3333-4444-555555555555"
    private_messages = client.get(f"/api/rooms/{room['id']}/controller/messages").json()
    assert [message["text"] for message in private_messages] == [
        "Follow up",
        "Controller resumed for private message.",
    ]


def test_controller_private_message_resumes_timestamped_codex_session(tmp_path, monkeypatch) -> None:
    session_id = "019ef3e4-eb46-7841-ba70-659fbfb3bbce"
    runtime_dir = tmp_path / "runtime" / "controller"
    sessions_dir = runtime_dir / ".codex" / "sessions" / "2026" / "06" / "23"
    sessions_dir.mkdir(parents=True)
    (sessions_dir / f"rollout-2026-06-23T18-52-06-{session_id}.jsonl").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("TMUX_PANE", "%0")
    monkeypatch.setattr(TmuxManager, "_split_pane", lambda _manager, _command: "%9")
    auth_file = tmp_path / "auth.json"
    auth_file.write_text("{}", encoding="utf-8")
    client = TestClient(create_app(Path.cwd(), tmp_path, auth_file))
    room = client.get("/api/rooms").json()[0]
    write_room_agents(
        tmp_path,
        room["id"],
        [agent_instance("controller-1", "controller", "stopped", None, str(runtime_dir))],
    )

    response = client.post(
        f"/api/rooms/{room['id']}/controller/messages",
        json={
            "actor_type": "user",
            "actor_id": "user",
            "actor_name": "User",
            "text": "Follow up",
            "kind": "message",
        },
    )

    assert response.status_code == 200
    updated = client.get(f"/api/rooms/{room['id']}").json()
    controller = updated["agents"][0]
    assert controller["state"] == "active"
    assert controller["pane_id"] == "%9"
    assert controller["codex_session_id"] == session_id


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
            "teams": [],
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
        json={"actor_id": "controller-1", "reason": "final report"},
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


def test_agent_mute_blocks_only_target_agent(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(TmuxManager, "_send_codex_prompt", lambda *args: None)
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
            "teams": [],
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
    open_response = client.post(
        f"/api/rooms/{room['id']}/discussion/open",
        json={"actor_id": "controller-1", "reason": "start contributions"},
    )
    assert open_response.status_code == 200
    assigned = client.post(
        f"/api/rooms/{room['id']}/assign",
        json={"actor_id": "controller-1", "agent_ids": ["critic-1", "builder-1"], "question": "Status?"},
    )
    assert assigned.status_code == 200

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


def test_room_done_closes_all_agent_panes(tmp_path, monkeypatch) -> None:
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
            "teams": [],
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
    assert stopped == [("controller-1", "%1", False), ("critic-1", "%2", False), ("builder-1", "%3", False)]
    updated = response.json()
    assert updated["state"] == "done"
    panes = {agent["id"]: agent["pane_id"] for agent in updated["agents"]}
    states = {agent["id"]: agent["state"] for agent in updated["agents"]}
    assert panes == {"controller-1": None, "critic-1": None, "builder-1": None}
    assert states["controller-1"] == "stopped"
    assert states["critic-1"] == "stopped"
    assert states["builder-1"] == "stopped"


def test_controller_private_message_resumes_after_room_done(tmp_path, monkeypatch) -> None:
    stopped = []
    resumed = []

    def fake_stop(_manager, agent, force) -> None:
        stopped.append((agent.id, agent.pane_id, force))

    def fake_resume(_manager, agent, text):
        resumed.append((agent.id, agent.pane_id, text))
        return "%9", "11111111-2222-3333-4444-555555555555"

    monkeypatch.setattr(TmuxManager, "stop", fake_stop)
    monkeypatch.setattr(TmuxManager, "resume_controller", fake_resume)
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
            "teams": [],
        },
    ).json()
    write_room_agents(
        tmp_path,
        room["id"],
        [
            agent_instance(
                "controller-1",
                "controller",
                "active",
                "%1",
                str(tmp_path / "runtime" / "controller"),
            ),
            agent_instance("critic-1", "critic", "active", "%2"),
        ],
    )

    done_response = client.post(
        f"/api/rooms/{room['id']}/done",
        json={"actor_id": "controller-1", "reason": "workshop complete"},
    )
    response = client.post(
        f"/api/rooms/{room['id']}/controller/messages",
        json={
            "actor_type": "user",
            "actor_id": "user",
            "actor_name": "User",
            "text": "Follow up after done",
            "kind": "message",
        },
    )

    assert done_response.status_code == 200
    assert stopped == [("controller-1", "%1", False), ("critic-1", "%2", False)]
    assert response.status_code == 200
    assert resumed == [("controller-1", None, "Follow up after done")]
    updated = client.get(f"/api/rooms/{room['id']}").json()
    controller = next(agent for agent in updated["agents"] if agent["id"] == "controller-1")
    assert updated["state"] == "done"
    assert controller["state"] == "active"
    assert controller["pane_id"] == "%9"
    assert controller["codex_session_id"] == "11111111-2222-3333-4444-555555555555"


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
            "teams": [],
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
            "teams": [],
        },
    ).json()

    response = client.post(
        f"/api/rooms/{room['id']}/agents",
        json={"template_id": "critic", "count": 1, "actor_id": "controller"},
    )

    assert response.status_code == 400
    assert "inside tmux" in response.json()["detail"]


def floor_room(tmp_path, monkeypatch):
    notices = []
    monkeypatch.setattr(TmuxManager, "_send_codex_prompt", lambda self, pane, text: notices.append((pane, text)))
    auth_file = tmp_path / "auth.json"
    auth_file.write_text("{}", encoding="utf-8")
    client = TestClient(create_app(Path.cwd(), tmp_path, auth_file))
    room = client.get("/api/rooms").json()[0]
    write_room_agents(tmp_path, room["id"], [
        agent_instance("controller-1", "controller", "active", "%1"),
        agent_instance("critic-1", "critic", "active", "%2"),
        agent_instance("builder-1", "builder", "active", "%3"),
    ])
    return client, f"/api/rooms/{room['id']}", notices


def public_post(client, base, actor_id, text, actor_type="agent"):
    return client.post(base + "/messages", json={
        "actor_type": actor_type, "actor_id": actor_id, "actor_name": actor_id,
        "text": text, "kind": "message",
    })


def test_floor_assignment_original_context_and_single_use(tmp_path, monkeypatch):
    client, base, notices = floor_room(tmp_path, monkeypatch)
    assert public_post(client, base, "critic-1", "Unassigned").status_code == 400
    # Actor labels cannot bypass the floor for a known regular agent.
    assert public_post(client, base, "critic-1", "Bypass", "controller").status_code == 400
    first = public_post(client, base, "user", "First original", "user").json()
    assigned = client.post(base + "/assign", json={
        "actor_id": "controller-1", "agent_ids": ["critic-1", "builder-1"], "question": "What do you think?",
    })
    assert assigned.status_code == 200
    assert len(notices) == 2
    assert "What do you think?" in notices[0][1]
    assert f"#{first['id']} user (user)\nFirst original" in notices[0][1]
    assert public_post(client, base, "critic-1", "First answer").status_code == 200
    assert public_post(client, base, "critic-1", "Second answer").status_code == 400
    recent = public_post(client, base, "builder-1", "Recent original").json()
    # Private whispers must never appear in assignment context.
    client.post(base + "/controller/messages", json={
        "actor_type": "controller", "actor_id": "controller-1", "actor_name": "Controller",
        "text": "Private secret", "kind": "message",
    })
    client.post(base + "/assign", json={
        "actor_id": "controller-1", "agent_ids": ["critic-1"], "question": "Confirm intent",
    })
    prompt = notices[-1][1]
    assert f"#{recent['id']} builder-1 (builder-1)\nRecent original" in prompt
    assert "First original" not in prompt
    assert "First answer" not in prompt
    assert "Private secret" not in prompt
    # Closing discussion blocks without consuming the grant.
    client.post(base + "/discussion/close", json={"actor_id": "controller-1", "reason": "pause"})
    assert public_post(client, base, "critic-1", "Closed").status_code == 400
    client.post(base + "/discussion/open", json={"actor_id": "controller-1", "reason": "resume"})
    assert public_post(client, base, "critic-1", "Resumed").status_code == 200
    assert len(client.get(base + "/messages").json()) == 4


def test_floor_request_defer_approve_decline_and_permissions(tmp_path, monkeypatch):
    client, base, notices = floor_room(tmp_path, monkeypatch)
    payload = {"actor_id": "critic-1", "kind": "反論", "target": "Builder", "summary": "Assumption needs evidence."}
    response = client.post(base + "/floor/requests", json=payload)
    assert response.status_code == 200
    request_id = response.json()["floor_requests"][0]["id"]
    path = base + f"/floor/requests/{request_id}"
    assert client.post(base + "/floor/requests", json=payload).status_code == 400
    assert public_post(client, base, "critic-1", "Not approved").status_code == 400
    assert client.post(path + "/approve", json={"actor_id": "builder-1"}).status_code == 400
    deferred = client.post(path + "/decline", json={"actor_id": "controller-1", "reason": "Later", "defer": True})
    assert deferred.json()["floor_requests"][0]["status"] == "deferred"
    assert deferred.json()["floor_requests"][0]["reason"] == "Later"
    assert client.post(base + "/floor/requests", json=payload).status_code == 400
    # Room state persists pending/deferred entries across application reload.
    reloaded = TestClient(create_app(Path.cwd(), tmp_path, tmp_path / "auth.json"))
    assert reloaded.get(base).json()["floor_requests"] == deferred.json()["floor_requests"]
    assert public_post(client, base, "critic-1", "Deferred").status_code == 400
    approved = client.post(path + "/approve", json={"actor_id": "controller-1"})
    assert approved.status_code == 200
    assert approved.json()["floor_requests"] == []
    assert notices[-1][0] == "%2" and request_id in notices[-1][1]
    assert public_post(client, base, "critic-1", "Approved").status_code == 200
    assert public_post(client, base, "critic-1", "Again").status_code == 400
    assert client.post(path + "/approve", json={"actor_id": "controller-1"}).status_code == 400
    second = client.post(base + "/floor/requests", json=payload).json()["floor_requests"][0]["id"]
    declined = client.post(base + f"/floor/requests/{second}/decline", json={
        "actor_id": "controller-1", "reason": "Outside scope", "defer": False,
    })
    assert declined.json()["floor_requests"] == []
    assert public_post(client, base, "critic-1", "Declined").status_code == 400
    assert client.post(base + "/assign", json={"actor_id": "critic-1", "agent_ids": ["critic-1"], "question": "Self assign"}).status_code == 400
    assert client.post(base + "/assign", json={"actor_id": "controller-1", "agent_ids": ["controller-1"], "question": "Controller"}).status_code == 400
    assert client.post(base + "/floor/requests", json=payload | {"actor_id": "controller-1"}).status_code == 400
    assert client.post(base + "/floor/requests", json=payload | {"kind": "Other"}).status_code == 422
    assert client.post(base + "/floor/requests", json=payload | {"summary": "  "}).status_code == 400


def test_named_reply_grants_only_the_named_regular_agent(tmp_path, monkeypatch):
    client, base, notices = floor_room(tmp_path, monkeypatch)
    assert public_post(client, base, "user", "宛先: 全体\nEveryone", "user").status_code == 200
    assert public_post(client, base, "critic-1", "No floor").status_code == 400
    assert public_post(client, base, "user", "Context\n宛先: Critic", "user").status_code == 200
    assert public_post(client, base, "critic-1", "Still no floor").status_code == 400
    assert public_post(client, base, "user", "宛先: Critic\nQuestion", "user").status_code == 200
    assert public_post(client, base, "builder-1", "Not named").status_code == 400
    assert public_post(client, base, "critic-1", "宛先: Builder\nAnswer and question").status_code == 200
    assert public_post(client, base, "critic-1", "Again").status_code == 400
    assert public_post(client, base, "builder-1", "宛先: Builder\nNo self renewal").status_code == 200
    assert public_post(client, base, "builder-1", "Again").status_code == 400
    assert public_post(client, base, "user", "宛先: Controller\nController question", "user").status_code == 200
    assert client.get(base).json()["floor_grants"] == []
    assert notices == []


def test_repeated_floor_grants_allow_only_one_post(tmp_path, monkeypatch):
    client, base, _ = floor_room(tmp_path, monkeypatch)
    assignment = {"actor_id": "controller-1", "agent_ids": ["critic-1"], "question": "Your view?"}
    for _ in range(2):
        assert client.post(base + "/assign", json=assignment).status_code == 200
    assert client.get(base).json()["floor_grants"] == ["critic-1"]
    assert public_post(client, base, "critic-1", "Answer").status_code == 200
    assert public_post(client, base, "critic-1", "Again").status_code == 400

    for _ in range(2):
        assert public_post(client, base, "user", "宛先: Critic\nQuestion", "user").status_code == 200
    request = client.post(base + "/floor/requests", json={
        "actor_id": "critic-1", "kind": "補足", "target": "全体", "summary": "Additional context.",
    }).json()["floor_requests"][0]
    assert client.post(base + f"/floor/requests/{request['id']}/approve", json={"actor_id": "controller-1"}).status_code == 200
    assert client.get(base).json()["floor_grants"] == ["critic-1"]
    assert public_post(client, base, "critic-1", "Reply").status_code == 200
    assert public_post(client, base, "critic-1", "Again").status_code == 400
