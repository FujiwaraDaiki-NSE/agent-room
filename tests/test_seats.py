from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from fastmcp import Client

from agent_room.mcp_server import create_agent_room_mcp
from agent_room.models import AgentInstance
from agent_room.server import create_app
from agent_room.store import Store
from agent_room.tmux_manager import TmuxManager


def agent(room_id, number, template="critic"):
    return AgentInstance(id=f"agent-{number}", room_id=room_id, template_id=template,
                         name=f"Agent {number}", short_name="A", role="review", personality="skeptical",
                         accent="#000000", avatar_url="/avatar.svg", state="active", pane_id=f"%{number}")


@pytest.fixture
def room_store(tmp_path):
    store = Store(tmp_path)
    room = store.current_room()
    for n in range(1, 4):
        store.add_agent(room.id, agent(room.id, n), "controller")
    return store, room.id


@pytest.mark.parametrize("kind,can_post,can_minutes", [("podium", True, False), ("discussion", True, False), ("scribe", False, True), ("audience", False, False)])
def test_seat_tools_layer_on_floor(room_store, kind, can_post, can_minutes):
    store, rid = room_store
    if kind != "audience":
        store.seat_action(rid, "agent-1", "assign", f"{kind}-1", None)
    store.set_agent_posting_closed(rid, False, "controller", "start")
    store.grant_floor(rid, ["agent-1"])
    if can_post:
        store.request_floor(rid, "agent-1", "質問", "全体", "確認")
        store.add_message(rid, "agent", "agent-1", "A", "発言", "message")
        with pytest.raises(ValueError, match="no floor"):
            store.add_message(rid, "agent", "agent-1", "A", "連投", "message")
    else:
        with pytest.raises(ValueError, match="seat"):
            store.request_floor(rid, "agent-1", "質問", "全体", "確認")
        with pytest.raises(ValueError, match="seat"):
            store.add_message(rid, "agent", "agent-1", "A", "発言", "message")
        assert "agent-1" in store.get_room(rid).floor_grants
    if can_minutes:
        store.update_board(rid, "agent-1", "minutes", "# 議事", None)
        assert store.update_board(rid, "agent-1", "minutes", "", None).minutes == ""
    else:
        with pytest.raises(ValueError, match="seat"):
            store.update_board(rid, "agent-1", "minutes", "# 議事", None)
    assert store.update_board(rid, "agent-1", "sticky", "付箋", None).stickies[-1].text == "付箋"


def test_move_request_leave_and_stop(room_store):
    store, rid = room_store
    assert [s.agent_id for s in store.get_room(rid).seats if s.kind == "audience"][:3] == ["agent-1", "agent-2", "agent-3"]
    store.seat_action(rid, "agent-1", "move", "audience-4", None)
    with pytest.raises(ValueError):
        store.seat_action(rid, "agent-1", "move", "audience-2", None)
    with pytest.raises(ValueError):
        store.seat_action(rid, "agent-1", "move", "podium-1", None)
    with pytest.raises(ValueError):
        store.seat_action(rid, "agent-1", "leave", None, None)
    requested = store.seat_action(rid, "agent-1", "request", "podium-1", "説明")
    assert next(s for s in requested.seats if s.id == "audience-4").agent_id == "agent-1"
    with pytest.raises(ValueError):
        store.seat_action(rid, "agent-1", "request", "scribe-1", "書記")
    store.decline_seat(rid, requested.seat_requests[0].id, "後で")
    room = store.seat_action(rid, "agent-1", "assign", "podium-1", None)
    assert room.floor_grants == ["agent-1"]
    room = store.seat_action(rid, "agent-1", "leave", None, None)
    assert next(s for s in room.seats if s.id == "audience-1").agent_id == "agent-1"
    room = store.update_agent(rid, "agent-1", {"state": "stopped"}, "controller", "agent.stopped")
    assert all(s.agent_id != "agent-1" for s in room.seats)
    assert "agent-1" not in room.floor_grants


@pytest.mark.parametrize("left,right,allowed", [("discussion-1", "discussion-8", True), ("discussion-2", "discussion-4", False), ("audience-6", "audience-7", False), ("audience-7", "audience-8", True), ("podium-1", "scribe-1", False), ("discussion-1", "audience-8", False)])
def test_whisper_adjacency_and_turns(room_store, left, right, allowed):
    store, rid = room_store
    store.seat_action(rid, "agent-1", "assign", left, None)
    store.seat_action(rid, "agent-2", "assign", right, None)
    if not allowed:
        with pytest.raises(ValueError):
            store.whisper(rid, "agent-1", "agent-2", "相談")
        return
    store.whisper(rid, "agent-1", "agent-2", "相談")
    with pytest.raises(ValueError, match="reply"):
        store.whisper(rid, "agent-1", "agent-2", "連投")
    store.whisper(rid, "agent-2", "agent-1", "返答")
    room = store.whisper(rid, "agent-1", "agent-2", "続き")
    assert len(room.whispers) == 3
    assert store.list_messages(rid, None) == []
    assert Store(store.data_dir).get_room(rid).whispers == room.whispers


@pytest.mark.parametrize("action,limit", [("whisper", 200), ("sticky", 80)])
def test_text_limits(room_store, action, limit):
    store, rid = room_store
    def send(text):
        if action == "whisper":
            return store.whisper(rid, "agent-1", "agent-2", text)
        return store.update_board(rid, "agent-1", "sticky", text, None)
    for text in ("", "あ" * (limit + 1)):
        with pytest.raises(ValueError):
            send(text)
    send("あ" * limit)


def test_deploy_capacity_and_whiteboard_versions(room_store):
    store, rid = room_store
    for n in range(4, 13):
        store.add_agent(rid, agent(rid, n), "controller")
    with pytest.raises(ValueError, match="full"):
        store.add_agent(rid, agent(rid, 13), "controller")
    assert len(store.get_room(rid).agents) == 12
    store.update_board(rid, "controller", "whiteboard", "<svg/>", "図1")
    store.update_meeting_status(rid, "controller", "deepen", "題", "要約", [], [], "検討")
    room = store.update_board(rid, "controller", "whiteboard", "<p>図2</p>", "図2")
    assert [v.title for v in room.whiteboard] == ["図1", "図2"]
    assert room.whiteboard[-1].phase == "deepen"
    assert room.whiteboard[0].id != room.whiteboard[1].id
    assert Store(store.data_dir).get_room(rid).whiteboard == room.whiteboard


@pytest.mark.asyncio
@pytest.mark.parametrize("viewer,controller,readable", [("agent-1", False, True), ("agent-2", False, True), ("agent-3", False, False), ("controller", True, False)])
async def test_mcp_whisper_visibility(room_store, viewer, controller, readable):
    store, rid = room_store
    room = store.whisper(rid, "agent-1", "agent-2", "秘密").model_dump()
    mcp = create_agent_room_mcp("http://server", rid, viewer, "A", Path.cwd() / "share", controller,
                                lambda *args: room)
    async with Client(mcp) as client:
        result = await client.call_tool("room_state", {})
        assert ("text" in result.data["whispers"][0]) is readable
        # Mutation responses also carry room state and must obey the same privacy rule.
        result = await client.call_tool("whiteboard_update" if controller else "sticky_post",
                                        {"title": "図", "html": "<svg/>"} if controller else {"text": "付箋"})
        assert ("text" in result.data["whispers"][0]) is readable
    assert room["whispers"][0]["text"] == "秘密"


def test_http_seats_board_whisper_and_deploy(tmp_path, monkeypatch):
    notices = []
    monkeypatch.setattr(TmuxManager, "_send_codex_prompt", lambda self, pane, text: notices.append((pane, text)))
    deployed = []
    def deploy(self, room, template, *args):
        deployed.append(template.id)
        return agent(room.id, len(room.agents), template.id)
    monkeypatch.setattr(TmuxManager, "deploy", deploy)
    client = TestClient(create_app(Path.cwd(), tmp_path, tmp_path / "auth"))
    store = Store(tmp_path)
    rid = store.current_room().id
    store.add_agent(rid, agent(rid, 0, "controller"), "user")
    base = f"/api/rooms/{rid}"
    for _ in range(2):
        response = client.post(base + "/agents", json={"actor_id": "agent-0", "template_id": "critic", "count": 1})
        assert response.status_code == 200
    assert "着席: audience-1" in notices[0][1]
    assert client.post(base + "/whispers", json={"actor_id": "agent-1", "to_agent_id": "agent-2", "text": "秘密"}).status_code == 200
    assert client.get(base).json()["whispers"][0]["text"] == "秘密"
    assert notices[-1] == ("%2", "Agent 1（audience-1）が小声で: 秘密")
    request = client.post(base + "/seats/requests", json={"actor_id": "agent-1", "seat_id": "scribe-1", "reason": "記録"}).json()["seat_requests"][0]
    path = base + f"/seats/requests/{request['id']}/approve"
    assert client.post(path, json={"actor_id": "agent-2"}).status_code == 400
    assert client.post(path, json={"actor_id": "agent-0"}).status_code == 200
    assert "書記席" in notices[-1][1]
    assert client.put(base + "/minutes", json={"actor_id": "agent-1", "markdown": "# 記録"}).json()["minutes"] == "# 記録"
    assert client.post(base + "/whiteboard", json={"actor_id": "agent-1", "title": "図", "html": "<svg/>"}).status_code == 400
    assert client.post(base + "/whiteboard", json={"actor_id": "agent-0", "title": "図", "html": "<svg/>"}).status_code == 200
    assert client.post(base + "/stickies", json={"actor_id": "agent-2", "text": "付箋"}).status_code == 200
    assert client.post(base + "/stickies/clear", json={"actor_id": "agent-2"}).status_code == 400
    assert client.post(base + "/stickies/clear", json={"actor_id": "agent-0"}).json()["stickies"] == []
    for n in range(3, 14):
        store.add_agent(rid, agent(rid, n), "controller")
    before = len(deployed)
    response = client.post(base + "/agents", json={"actor_id": "agent-0", "template_id": "critic", "count": 1})
    assert response.status_code == 400
    assert "full" in response.json()["detail"]
    assert len(deployed) == before


def test_websocket_board_and_seat_events(tmp_path, monkeypatch):
    monkeypatch.setattr(TmuxManager, "_send_codex_prompt", lambda *args: None)
    client = TestClient(create_app(Path.cwd(), tmp_path, tmp_path / "auth"))
    store = Store(tmp_path)
    rid = store.current_room().id
    for n, template in ((0, "controller"), (1, "critic"), (2, "critic")):
        store.add_agent(rid, agent(rid, n, template), "user")
    base = f"/api/rooms/{rid}"
    with client.websocket_connect(f"/ws/rooms/{rid}") as ws:
        operations = [
            ("post", "/seats/move", {"actor_id": "agent-1", "seat_id": "audience-3"}, "seat.updated"),
            ("post", "/whispers", {"actor_id": "agent-1", "to_agent_id": "agent-2", "text": "小声"}, "whisper.created"),
            ("post", "/stickies", {"actor_id": "agent-1", "text": "付箋"}, "sticky.updated"),
            ("post", "/seats/assign", {"actor_id": "agent-0", "agent_id": "agent-1", "seat_id": "scribe-1"}, "seat.updated"),
            ("put", "/minutes", {"actor_id": "agent-1", "markdown": "# 記録"}, "minutes.updated"),
            ("post", "/whiteboard", {"actor_id": "agent-0", "title": "図", "html": "<svg/>"}, "whiteboard.updated"),
        ]
        for method, path, payload, event_type in operations:
            response = getattr(client, method)(base + path, json=payload)
            assert response.status_code == 200
            event = ws.receive_json()
            assert event["type"] == event_type
            assert event["room"] == response.json()
            if event_type == "whisper.created":
                assert event["whisper"]["text"] == "小声"
                assert event["whisper"] == event["room"]["whispers"][-1]
