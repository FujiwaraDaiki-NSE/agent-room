from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

from fastmcp import FastMCP

from .share import EXCLUDED, read_path


RequestFn = Callable[[str, str, dict[str, Any] | None], Any]


def create_agent_room_mcp(
    server_url: str,
    room_id: str,
    agent_id: str,
    agent_name: str,
    share_root: Path,
    is_controller: bool,
    request_fn: RequestFn,
) -> FastMCP:
    instructions = (
        "Use these tools to participate in the Agent Room. "
        "Read the room before posting, post concise messages to the public room, "
        "Regular agents post only with a one-use floor; use floor_request to ask to speak. "
        "Read room_state for pending and deferred floor requests. "
        "and mark yourself done only when your assigned termination condition is met."
    )
    if is_controller:
        instructions += (
            " Controller tools can manage agents, meeting status, and private user-side whispers. "
            "Use room_finish, not room_done, to finish the room."
        )
    mcp = FastMCP(name="Agent Room", instructions=instructions)

    def redact(value: Any) -> Any:
        if isinstance(value, list):
            return [redact(item) for item in value]
        if isinstance(value, dict):
            result = {key: redact(item) for key, item in value.items()}
            if "from_agent_id" in result and "to_agent_id" in result:
                if is_controller or agent_id not in (result["from_agent_id"], result["to_agent_id"]):
                    result.pop("text", None)
            return result
        return value

    def call(method: str, target_url: str, payload: dict[str, Any] | None) -> Any:
        return redact(request_fn(method, target_url, payload))

    base_url = server_url.rstrip("/")
    actor_type = "controller" if is_controller else "agent"
    resolved_share_root = share_root.resolve()

    def url(path: str) -> str:
        return f"{base_url}{path}"

    def selected_share_contexts() -> list[str]:
        room = call("GET", url(f"/api/rooms/{room_id}"), None)
        return list(room["share_contexts"])

    def handout_path(path: str, selections: list[str]) -> Path:
        if not path or Path(path).is_absolute() or any(part in {"", ".", ".."} | EXCLUDED for part in path.split("/")):
            raise ValueError("share path must stay inside the selected context")
        for selected in selections:
            if path != selected and not path.startswith(selected + "/"):
                continue
            root = (resolved_share_root / selected).resolve()
            target = (resolved_share_root / path).resolve()
            if (target == root or root.is_dir() and target.is_relative_to(root)) and target.exists():
                if target != root and any(part in EXCLUDED for part in target.relative_to(root).parts):
                    raise ValueError("excluded share directory")
                return target
        raise ValueError("share path must stay inside the selected context")

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
    def room_state() -> dict[str, Any]:
        """Read room state, including floor grants and pending/deferred requests."""
        return call("GET", url(f"/api/rooms/{room_id}"), None)

    if not is_controller:

        @mcp.tool
        def floor_request(kind: Literal["質問", "反論", "補足"], target: str, summary: str) -> dict[str, Any]:
            """Raise one hand to speak. Only one unresolved request per agent is allowed."""
            return call("POST", url(f"/api/rooms/{room_id}/floor/requests"), {
                "actor_id": agent_id, "kind": kind, "target": target, "summary": summary,
            })

    def seat_call(path: str, payload: dict[str, Any], method: str = "POST") -> dict[str, Any]:
        return call(method, url(f"/api/rooms/{room_id}/{path}"), {"actor_id": agent_id, **payload})

    if not is_controller:
        @mcp.tool
        def seat_move(seat_id: str) -> dict[str, Any]:
            """Move freely to an empty seat of the same kind."""
            return seat_call("seats/move", {"seat_id": seat_id})

        @mcp.tool
        def seat_request(seat_id: str, reason: str) -> dict[str, Any]:
            """Request controller approval for a different seat kind."""
            return seat_call("seats/requests", {"seat_id": seat_id, "reason": reason})

        @mcp.tool
        def seat_leave() -> dict[str, Any]:
            """Return to the first empty audience seat."""
            return seat_call("seats/leave", {})

        @mcp.tool
        def seat_whisper(to_agent_id: str, text: str) -> dict[str, Any]:
            """Whisper 1–200 characters to a neighbor; wait for their reply."""
            return seat_call("whispers", {"to_agent_id": to_agent_id, "text": text})

        @mcp.tool
        def sticky_post(text: str) -> dict[str, Any]:
            """Post a 1–80 character sticky to the whiteboard."""
            return seat_call("stickies", {"text": text})

        @mcp.tool
        def minutes_update(markdown: str) -> dict[str, Any]:
            """Replace meeting minutes from the scribe seat."""
            return seat_call("minutes", {"markdown": markdown}, "PUT")
    else:
        @mcp.tool
        def seat_assign(agent_id: str, seat_id: str) -> dict[str, Any]:
            """Assign a regular agent to an empty seat."""
            return seat_call("seats/assign", {"agent_id": agent_id, "seat_id": seat_id})

        @mcp.tool
        def seat_approve(request_id: str) -> dict[str, Any]:
            """Approve a seat change request."""
            return seat_call(f"seats/requests/{request_id}/approve", {})

        @mcp.tool
        def seat_decline(request_id: str, reason: str) -> dict[str, Any]:
            """Decline a seat change with a reason."""
            return seat_call(f"seats/requests/{request_id}/decline", {"reason": reason})

        @mcp.tool
        def sticky_clear() -> dict[str, Any]:
            """Clear all sticky notes."""
            return seat_call("stickies/clear", {})

        @mcp.tool
        def whiteboard_update(title: str, html: str) -> dict[str, Any]:
            """Append an HTML whiteboard version; draw with SVG/CSS."""
            return seat_call("whiteboard", {"title": title, "html": html})

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
        termination condition is satisfied. Controllers should use room_finish,
        not this tool, to finish the room.

        Args:
            reason: Short reason why this agent is done.

        Returns:
            The updated room state.
        """
        payload = {"actor_id": agent_id, "reason": reason}
        return call("POST", url(f"/api/rooms/{room_id}/agents/{agent_id}/done"), payload)

    @mcp.tool
    def share_contexts() -> list[str]:
        """List the current selected handout paths (files or directories) for this room.

        Use this before reading shared files. Selections can change during the meeting.

        Returns:
            Selected share/-relative file and directory paths.
        """
        return selected_share_contexts()

    @mcp.tool
    def share_list(path: str) -> list[dict[str, Any]]:
        """List direct children of a shared directory using one share/-relative path.

        Only selected paths and their descendants are accessible. Returned
        relative_path values are relative to share/ and can be passed directly
        to share_list or share_read.
        """
        selections = selected_share_contexts()
        target = handout_path(path, selections)
        if not target.is_dir():
            raise ValueError(f"share path is not a directory: {path}")
        items = []
        for child in sorted(target.iterdir(), key=lambda item: item.name):
            child_path = f"{path}/{child.name}"
            try:
                handout_path(child_path, selections)
            except ValueError:
                continue
            items.append({"name": child.name, "relative_path": child_path,
                          "type": "dir" if child.is_dir() else "file", "size": child.stat().st_size if child.is_file() else None})
        record_access(path, "list")
        return items

    def record_access(path: str, operation: str) -> None:
        call("POST", url(f"/api/rooms/{room_id}/share/access"), {"actor_id": agent_id, "path": path, "operation": operation})

    @mcp.tool
    def share_read(path: str) -> dict[str, Any]:
        """Read a shared text file using one share/-relative path, e.g. design_db/docs/spec.md.

        Only selected files and files under selected directories are accessible.
        """
        result = read_path(handout_path(path, selected_share_contexts()))
        record_access(path, "read")
        return {"path": path, **result}

    if is_controller:

        @mcp.tool
        def agent_assign(agent_ids: list[str], question: str) -> dict[str, Any]:
            """Grant one post per named agent and send the question with original public statements."""
            return call("POST", url(f"/api/rooms/{room_id}/assign"), {
                "actor_id": agent_id, "agent_ids": agent_ids, "question": question,
            })

        @mcp.tool
        def floor_approve(request_id: str) -> dict[str, Any]:
            """Approve a pending or deferred request and notify the agent of its one-use floor."""
            return call("POST", url(f"/api/rooms/{room_id}/floor/requests/{request_id}/approve"), {"actor_id": agent_id})

        @mcp.tool
        def floor_decline(request_id: str, reason: str, defer: bool) -> dict[str, Any]:
            """Decline a request, keeping it in the queue when defer is true."""
            return call("POST", url(f"/api/rooms/{room_id}/floor/requests/{request_id}/decline"), {
                "actor_id": agent_id, "reason": reason, "defer": defer,
            })

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
        def planned_agents() -> list[str]:
            """List planned regular agent template ids.

            These are the regular agents selected when the room was started.
            Deploy them with agent_deploy only when their temporary viewpoint
            is needed for the current workshop phase.

            Returns:
                Planned regular agent template ids.
            """
            room = call("GET", url(f"/api/rooms/{room_id}"), None)
            return list(room["planned_template_ids"])

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
        def room_status_update(
            phase: str,
            topic: str,
            summary: str,
            decisions: list[str],
            open_questions: list[str],
            next: str,
        ) -> dict[str, Any]:
            """Update the user-visible meeting status.

            Use this before phase changes, after each round, and before the
            final controller report so the user can see where the meeting is.

            Args:
                phase: Current meeting phase.
                topic: Active discussion topic.
                summary: Short current summary.
                decisions: Decisions already made.
                open_questions: Important unresolved questions.
                next: Next controller action.

            Returns:
                The updated room state.
            """
            payload = {
                "actor_id": agent_id,
                "phase": phase,
                "topic": topic,
                "summary": summary,
                "decisions": decisions,
                "open_questions": open_questions,
                "next": next,
            }
            return call("PUT", url(f"/api/rooms/{room_id}/status"), payload)

        @mcp.tool
        def room_close_discussion(reason: str) -> dict[str, Any]:
            """Close public discussion for regular agents.

            After this tool is used, regular agents cannot post public messages.
            The controller and user can still post, so use this before a final
            controller report or when the user asks to end the discussion.

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
        def room_finish(reason: str) -> dict[str, Any]:
            """Finish the room and close all agent panes.

            Use this after the controller final report when the workshop
            outcome is complete. This marks the room done and closes every
            Codex agent pane, including the controller pane. A later private
            controller message can resume the controller session.

            Args:
                reason: Short reason why the workshop is complete.

            Returns:
                The updated room state.
            """
            payload = {"actor_id": agent_id, "reason": reason}
            return call("POST", url(f"/api/rooms/{room_id}/done"), payload)

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
