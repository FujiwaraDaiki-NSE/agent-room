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
        is_controller=False,
        request_fn=fake_request,
    )

    async with Client(mcp) as client:
        tools = await client.list_tools()
        names = {tool.name for tool in tools}
        result = await client.call_tool("room_post", {"text": "Public"})

    assert names == {"room_read", "room_post", "room_done"}
    assert result.data["payload"] == {
        "actor_type": "agent",
        "actor_id": "critic-1",
        "actor_name": "Critic",
        "text": "Public",
        "kind": "message",
    }
    assert calls[-1][0] == "POST"
    assert calls[-1][1] == "http://server/api/rooms/room-1/messages"


async def test_controller_mcp_tools_include_private_and_lifecycle_tools() -> None:
    calls = []

    def fake_request(method, url, payload):
        calls.append((method, url, payload))
        return {"ok": True, "payload": payload}

    mcp = create_agent_room_mcp(
        server_url="http://server",
        room_id="room-1",
        agent_id="controller-1",
        agent_name="Controller",
        is_controller=True,
        request_fn=fake_request,
    )

    async with Client(mcp) as client:
        tools = await client.list_tools()
        names = {tool.name for tool in tools}
        schema_by_name = {tool.name: tool.inputSchema for tool in tools}
        result = await client.call_tool("controller_post", {"text": "Private"})

    assert {
        "room_read",
        "room_post",
        "room_done",
        "controller_read",
        "controller_post",
        "agent_deploy",
        "agent_stop",
        "agent_goal",
        "agent_config",
    } == names
    assert schema_by_name["agent_deploy"]["required"] == ["template_id", "count"]
    assert schema_by_name["agent_stop"]["required"] == ["target_agent_id", "reason", "force"]
    assert result.data["payload"]["actor_type"] == "controller"
    assert calls[-1][1] == "http://server/api/rooms/room-1/controller/messages"
