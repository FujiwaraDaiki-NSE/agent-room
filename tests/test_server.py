from pathlib import Path

from fastapi.testclient import TestClient

from agent_room.server import create_app


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
            "termination": "All agents done",
            "templates": [],
        },
    )

    assert response.status_code == 200
    room = response.json()
    assert room["goal"] == "Discuss architecture"
    assert room["agents"] == []

    messages = client.get(f"/api/rooms/{room['id']}/messages").json()
    assert messages[0]["kind"] == "goal"


def test_controller_private_messages_are_separate(tmp_path) -> None:
    auth_file = tmp_path / "auth.json"
    auth_file.write_text("{}", encoding="utf-8")
    client = TestClient(create_app(Path.cwd(), tmp_path, auth_file))
    room = client.get("/api/rooms").json()[0]

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
            "termination": "All agents done",
            "templates": [],
        },
    ).json()

    response = client.post(
        f"/api/rooms/{room['id']}/agents",
        json={"template_id": "critic", "count": 1, "actor_id": "controller"},
    )

    assert response.status_code == 400
    assert "inside tmux" in response.json()["detail"]
