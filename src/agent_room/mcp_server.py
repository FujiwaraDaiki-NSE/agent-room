from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastmcp import FastMCP


RequestFn = Callable[[str, str, dict[str, Any] | None], Any]


def create_agent_room_mcp(
    server_url: str,
    room_id: str,
    agent_id: str,
    agent_name: str,
    is_controller: bool,
    request_fn: RequestFn,
) -> FastMCP:
    instructions = (
        "Use these tools to participate in the Agent Room. "
        "Read the room before posting, post concise messages to the public room, "
        "and mark yourself done only when your assigned termination condition is met."
    )
    if is_controller:
        instructions += " Controller tools can manage agents and private user-side whispers."
    mcp = FastMCP(name="Agent Room", instructions=instructions)
    call = request_fn
    base_url = server_url.rstrip("/")
    actor_type = "controller" if is_controller else "agent"

    def url(path: str) -> str:
        return f"{base_url}{path}"

    def selected_share_contexts() -> list[str]:
        room = call("GET", url(f"/api/rooms/{room_id}"), None)
        return list(room.get("share_contexts", []))

    def context_root(context_name: str) -> Path:
        contexts = selected_share_contexts()
        if context_name not in contexts:
            raise ValueError(f"share context not selected: {context_name}")
        if Path(context_name).name != context_name:
            raise ValueError(f"share context must be a directory name: {context_name}")
        root = (Path.cwd() / "share" / context_name).resolve()
        share_root = (Path.cwd() / "share").resolve()
        if share_root not in root.parents:
            raise ValueError(f"share context path escapes share root: {context_name}")
        if not root.is_dir():
            raise ValueError(f"share context not found: {context_name}")
        return root

    def context_path(context_name: str, relative_path: str) -> Path:
        root = context_root(context_name)
        target = (root / relative_path).resolve()
        if target != root and root not in target.parents:
            raise ValueError("share path must stay inside the selected context")
        return target

    @mcp.tool
    def room_read(after_id: int | None = None) -> list[dict[str, Any]]:
        """Read public room messages.

        Use this before deciding what to say next. If after_id is set, only
        messages with a larger id are returned.

        Args:
            after_id: Optional last seen message id.

        Returns:
            Public room messages in chronological order.
        """
        query = ""
        if after_id is not None:
            query = "?" + urllib.parse.urlencode({"after_id": after_id})
        return call("GET", url(f"/api/rooms/{room_id}/messages{query}"), None)

    @mcp.tool
    def room_post(text: str) -> dict[str, Any]:
        """Post a public message to the room.

        This tool creates a new message visible to every participant. Use it for
        meeting contributions, questions, objections, and summaries.

        Args:
            text: Message text to publish.

        Returns:
            The created message.
        """
        payload = {
            "actor_type": actor_type,
            "actor_id": agent_id,
            "actor_name": agent_name,
            "text": text,
            "kind": "message",
        }
        return call("POST", url(f"/api/rooms/{room_id}/messages"), payload)

    @mcp.tool
    def room_done(reason: str) -> dict[str, Any]:
        """Mark this agent as done.

        This tool updates this agent's state. Use it only when your assigned
        termination condition is satisfied.

        Args:
            reason: Short reason why this agent is done.

        Returns:
            The updated room state.
        """
        payload = {"actor_id": agent_id, "reason": reason}
        return call("POST", url(f"/api/rooms/{room_id}/agents/{agent_id}/done"), payload)

    @mcp.tool
    def share_contexts() -> list[str]:
        """List selected shared context names for this room.

        Use this before reading shared files. Only contexts selected when the
        room was created are returned.

        Returns:
            Selected shared context names.
        """
        return selected_share_contexts()

    @mcp.tool
    def share_list(context_name: str, relative_path: str) -> list[dict[str, Any]]:
        """List files and directories inside a selected shared context.

        Args:
            context_name: Selected context name such as design_db.
            relative_path: Context-relative directory path. Use "." for root.

        Returns:
            Direct children with name, relative_path, type, and size.
        """
        target = context_path(context_name, relative_path)
        if not target.is_dir():
            raise ValueError(f"share path is not a directory: {relative_path}")
        root = context_root(context_name)
        items = []
        for child in sorted(target.iterdir(), key=lambda item: item.name):
            item_type = "dir" if child.is_dir() else "file"
            size = child.stat().st_size if child.is_file() else None
            items.append(
                {
                    "name": child.name,
                    "relative_path": str(child.relative_to(root)),
                    "type": item_type,
                    "size": size,
                }
            )
        return items

    @mcp.tool
    def share_read(context_name: str, relative_path: str) -> dict[str, Any]:
        """Read a text file from a selected shared context.

        Args:
            context_name: Selected context name such as design_db.
            relative_path: Context-relative file path.

        Returns:
            File content and truncation metadata.
        """
        target = context_path(context_name, relative_path)
        if not target.is_file():
            raise ValueError(f"share path is not a file: {relative_path}")
        data = target.read_bytes()
        limit = 60_000
        truncated = len(data) > limit
        text = data[:limit].decode("utf-8", errors="replace")
        return {
            "context_name": context_name,
            "relative_path": str(target.relative_to(context_root(context_name))),
            "content": text,
            "truncated": truncated,
            "bytes": len(data),
        }

    if is_controller:

        @mcp.tool
        def controller_read(after_id: int | None = None) -> list[dict[str, Any]]:
            """Read private controller messages.

            Use this for user-side whispers that should not appear in the
            public room.

            Args:
                after_id: Optional last seen private message id.

            Returns:
                Private controller messages in chronological order.
            """
            query = ""
            if after_id is not None:
                query = "?" + urllib.parse.urlencode({"after_id": after_id})
            return call("GET", url(f"/api/rooms/{room_id}/controller/messages{query}"), None)

        @mcp.tool
        def controller_post(text: str) -> dict[str, Any]:
            """Post a private controller message.

            This tool creates a message visible only in the controller panel.

            Args:
                text: Private message text.

            Returns:
                The created private message.
            """
            payload = {
                "actor_type": "controller",
                "actor_id": agent_id,
                "actor_name": agent_name,
                "text": text,
                "kind": "message",
            }
            return call("POST", url(f"/api/rooms/{room_id}/controller/messages"), payload)

        @mcp.tool
        def agent_deploy(template_id: str, count: int) -> dict[str, Any]:
            """Deploy one or more agents into the room.

            This tool creates new Codex agent panes from a template.

            Args:
                template_id: Agent template id to deploy.
                count: Number of agents to deploy.

            Returns:
                The updated room state.
            """
            payload = {"template_id": template_id, "count": count, "actor_id": agent_id}
            return call("POST", url(f"/api/rooms/{room_id}/agents"), payload)

        @mcp.tool
        def agent_stop(target_agent_id: str, reason: str, force: bool) -> dict[str, Any]:
            """Stop an agent pane.

            This tool stops only the target agent, not the meeting server.

            Args:
                target_agent_id: Agent id to stop.
                reason: Short reason for stopping.
                force: Whether to kill the pane without graceful exit.

            Returns:
                The updated room state.
            """
            payload = {"actor_id": agent_id, "reason": reason, "force": force}
            return call("POST", url(f"/api/rooms/{room_id}/agents/{target_agent_id}/stop"), payload)

        @mcp.tool
        def agent_goal(
            target_agent_id: str,
            goal: str,
            controller_termination: str,
            agent_termination: str,
        ) -> dict[str, Any]:
            """Send a new goal and termination conditions to an agent.

            This tool updates an existing agent's active goal.

            Args:
                target_agent_id: Agent id to update.
                goal: Goal prompt to send.
                controller_termination: Termination condition for the controller.
                agent_termination: Termination condition for regular agents.

            Returns:
                The updated room state.
            """
            payload = {
                "actor_id": agent_id,
                "goal": goal,
                "controller_termination": controller_termination,
                "agent_termination": agent_termination,
            }
            return call("POST", url(f"/api/rooms/{room_id}/agents/{target_agent_id}/goal"), payload)

        @mcp.tool
        def agent_config(
            target_agent_id: str,
            relative_path: str,
            content: str,
            reason: str,
        ) -> dict[str, Any]:
            """Update a deployed agent runtime config file.

            This tool writes inside the target agent's copied runtime directory.
            It does not modify template originals.

            Args:
                target_agent_id: Agent id to update.
                relative_path: Runtime-relative path such as .codex/config.toml.
                content: New file content.
                reason: Short reason for the change.

            Returns:
                The updated room state.
            """
            payload = {
                "actor_id": agent_id,
                "relative_path": relative_path,
                "content": content,
                "reason": reason,
            }
            return call("POST", url(f"/api/rooms/{room_id}/agents/{target_agent_id}/config"), payload)

        @mcp.tool
        def room_close_discussion(reason: str) -> dict[str, Any]:
            """Close public discussion for regular agents.

            After this tool is used, regular agents cannot post public messages.
            The controller and user can still post, so use this before a final
            controller summary or when the user asks to end the discussion.

            Args:
                reason: Short reason for closing discussion.

            Returns:
                The updated room state.
            """
            payload = {"actor_id": agent_id, "reason": reason}
            return call("POST", url(f"/api/rooms/{room_id}/discussion/close"), payload)

        @mcp.tool
        def room_open_discussion(reason: str) -> dict[str, Any]:
            """Reopen public discussion for regular agents.

            Args:
                reason: Short reason for reopening discussion.

            Returns:
                The updated room state.
            """
            payload = {"actor_id": agent_id, "reason": reason}
            return call("POST", url(f"/api/rooms/{room_id}/discussion/open"), payload)

        @mcp.tool
        def agent_mute(target_agent_id: str, reason: str) -> dict[str, Any]:
            """Mute one regular agent's public messages.

            The target agent remains running and can still read the room or
            mark itself done, but public posts are rejected until unmuted.

            Args:
                target_agent_id: Agent id to mute.
                reason: Short reason for muting.

            Returns:
                The updated room state.
            """
            payload = {"actor_id": agent_id, "reason": reason}
            return call("POST", url(f"/api/rooms/{room_id}/agents/{target_agent_id}/mute"), payload)

        @mcp.tool
        def agent_unmute(target_agent_id: str, reason: str) -> dict[str, Any]:
            """Unmute one regular agent's public messages.

            Args:
                target_agent_id: Agent id to unmute.
                reason: Short reason for unmuting.

            Returns:
                The updated room state.
            """
            payload = {"actor_id": agent_id, "reason": reason}
            return call("POST", url(f"/api/rooms/{room_id}/agents/{target_agent_id}/unmute"), payload)

    return mcp


def request(method: str, url: str, payload: dict[str, Any] | None = None) -> Any:
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, method=method, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8")
        raise RuntimeError(f"request failed: {exc.code} {body}") from exc
