from pathlib import Path

import pytest
from fastmcp import Client

from agent_room.mcp_server import create_agent_room_mcp


pytestmark = pytest.mark.asyncio


async def test_regular_agent_mcp_tools_post_as_agent() -> None:
    calls = []

    def fake_request(method, url, payload):
        calls.append((method, url, payload))
        if method == "GET":
            return [{"id": 1, "text": "Hello"}]
        return {"ok": True, "payload": payload}

    mcp = create_agent_room_mcp(
        server_url="http://server",
        room_id="room-1",
        agent_id="critic-1",
        agent_name="Critic",
        share_root=Path.cwd() / "share",
        is_controller=False,
        request_fn=fake_request,
    )

    async with Client(mcp) as client:
        tools = await client.list_tools()
        names = {tool.name for tool in tools}
        result = await client.call_tool("room_post", {"text": "Public"})

    assert names == {"room_read", "room_post", "room_done", "share_contexts", "share_list", "share_read"}
    assert result.data["payload"] == {
        "actor_type": "agent",
        "actor_id": "critic-1",
        "actor_name": "Critic",
        "text": "Public",
        "kind": "message",
    }
    assert calls[-1][0] == "POST"
    assert calls[-1][1] == "http://server/api/rooms/room-1/messages"


async def test_share_mcp_tools_read_selected_context(tmp_path) -> None:
    context = tmp_path / "runtime" / "share" / "alpha"
    context.mkdir(parents=True)
    (context / "README.md").write_text("Alpha context\n", encoding="utf-8")

    def fake_request(method, url, payload):
        if method == "GET" and url == "http://server/api/rooms/room-1":
            return {"share_contexts": ["alpha"]}
        return {"ok": True, "payload": payload}

    mcp = create_agent_room_mcp(
        server_url="http://server",
        room_id="room-1",
        agent_id="critic-1",
        agent_name="Critic",
        share_root=tmp_path / "runtime" / "share",
        is_controller=False,
        request_fn=fake_request,
    )

    async with Client(mcp) as client:
        contexts = await client.call_tool("share_contexts", {})
        items = await client.call_tool("share_list", {"context_name": "alpha", "relative_path": "."})
        content = await client.call_tool("share_read", {"context_name": "alpha", "relative_path": "README.md"})

    assert contexts.data == ["alpha"]
    assert items.data == [
        {
            "name": "README.md",
            "relative_path": "README.md",
            "type": "file",
            "size": len("Alpha context\n"),
        }
    ]
    assert content.data["content"] == "Alpha context\n"
    assert content.data["truncated"] is False


async def test_controller_mcp_tools_include_private_and_lifecycle_tools() -> None:
    calls = []

    def fake_request(method, url, payload):
        calls.append((method, url, payload))
        if method == "GET" and url == "http://server/api/rooms/room-1":
            return {"planned_template_ids": ["critic", "researcher"]}
        return {"ok": True, "payload": payload}

    mcp = create_agent_room_mcp(
        server_url="http://server",
        room_id="room-1",
        agent_id="controller-1",
        agent_name="Controller",
        share_root=Path.cwd() / "share",
        is_controller=True,
        request_fn=fake_request,
    )

    async with Client(mcp) as client:
        tools = await client.list_tools()
        names = {tool.name for tool in tools}
        schema_by_name = {tool.name: tool.inputSchema for tool in tools}
        result = await client.call_tool("controller_post", {"text": "Private"})
        status_result = await client.call_tool(
            "room_status_update",
            {
                "phase": "Synthesis",
                "topic": "Evidence registry",
                "summary": "Converging on traceability.",
                "decisions": ["Use registry"],
                "open_questions": ["Owner granularity"],
                "next": "Ask final objections",
            },
        )
        planned_result = await client.call_tool("planned_agents", {})
        close_result = await client.call_tool("room_close_discussion", {"reason": "final summary"})
        finish_result = await client.call_tool("room_finish", {"reason": "workshop complete"})
        mute_result = await client.call_tool("agent_mute", {"target_agent_id": "critic-1", "reason": "over limit"})

    assert {
        "room_read",
        "room_post",
        "room_done",
        "share_contexts",
        "share_list",
        "share_read",
        "controller_read",
        "controller_post",
        "agent_deploy",
        "planned_agents",
        "agent_stop",
        "agent_goal",
        "agent_config",
        "room_status_update",
        "room_close_discussion",
        "room_open_discussion",
        "room_finish",
        "agent_mute",
        "agent_unmute",
    } == names
    assert schema_by_name["agent_deploy"]["required"] == ["template_id", "count"]
    assert schema_by_name["planned_agents"].get("required", []) == []
    assert schema_by_name["agent_stop"]["required"] == ["target_agent_id", "reason", "force"]
    assert schema_by_name["room_status_update"]["required"] == [
        "phase",
        "topic",
        "summary",
        "decisions",
        "open_questions",
        "next",
    ]
    assert schema_by_name["room_close_discussion"]["required"] == ["reason"]
    assert schema_by_name["room_finish"]["required"] == ["reason"]
    assert schema_by_name["agent_mute"]["required"] == ["target_agent_id", "reason"]
    assert result.data["payload"]["actor_type"] == "controller"
    assert status_result.data["payload"] == {
        "actor_id": "controller-1",
        "phase": "Synthesis",
        "topic": "Evidence registry",
        "summary": "Converging on traceability.",
        "decisions": ["Use registry"],
        "open_questions": ["Owner granularity"],
        "next": "Ask final objections",
    }
    assert planned_result.data == ["critic", "researcher"]
    assert close_result.data["payload"] == {"actor_id": "controller-1", "reason": "final summary"}
    assert finish_result.data["payload"] == {"actor_id": "controller-1", "reason": "workshop complete"}
    assert mute_result.data["payload"] == {"actor_id": "controller-1", "reason": "over limit"}
    assert calls[-6][1] == "http://server/api/rooms/room-1/controller/messages"
    assert calls[-5][0] == "PUT"
    assert calls[-5][1] == "http://server/api/rooms/room-1/status"
    assert calls[-4][1] == "http://server/api/rooms/room-1"
    assert calls[-3][1] == "http://server/api/rooms/room-1/discussion/close"
    assert calls[-2][1] == "http://server/api/rooms/room-1/done"
    assert calls[-1][1] == "http://server/api/rooms/room-1/agents/critic-1/mute"
