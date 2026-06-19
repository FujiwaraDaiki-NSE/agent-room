from pathlib import Path

from fastapi.testclient import TestClient

from agent_room.server import create_app


def test_api_room_without_agents(tmp_path) -> None:
    client = TestClient(create_app(Path.cwd(), tmp_path))

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


def test_deploy_requires_tmux(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("TMUX_PANE", raising=False)
    client = TestClient(create_app(Path.cwd(), tmp_path))
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
