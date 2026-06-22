from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .models import (
    AgentGoalRequest,
    CreateRoomRequest,
    DeployAgentRequest,
    MarkDoneRequest,
    PostMessageRequest,
    StopAgentRequest,
    UpdateAgentConfigRequest,
)
from .store import Store
from .templates import TemplateError, TemplateRegistry
from .tmux_manager import TmuxError, TmuxManager


class Hub:
    def __init__(self) -> None:
        self.clients: dict[str, list[WebSocket]] = {}

    async def connect(self, room_id: str, websocket: WebSocket) -> None:
        await websocket.accept()
        if room_id not in self.clients:
            self.clients[room_id] = []
        self.clients[room_id].append(websocket)

    def disconnect(self, room_id: str, websocket: WebSocket) -> None:
        sockets = self.clients.get(room_id, [])
        if websocket in sockets:
            sockets.remove(websocket)

    async def broadcast(self, room_id: str, payload: dict[str, Any]) -> None:
        for websocket in list(self.clients.get(room_id, [])):
            await websocket.send_json(payload)


def create_app(project_root: Path, data_dir: Path, codex_auth_file: Path) -> FastAPI:
    registry = TemplateRegistry(project_root)
    store = Store(data_dir)
    tmux = TmuxManager(project_root, data_dir, codex_auth_file)
    share_root = project_root / "share"
    hub = Hub()
    app = FastAPI(title="Agent Room")

    static_dir = Path(__file__).parent / "static"
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    @app.middleware("http")
    async def no_cache_static(request: Request, call_next: Any) -> Any:
        response = await call_next(request)
        if request.url.path == "/" or request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(static_dir / "index.html")

    @app.get("/api/templates")
    def list_templates() -> list[dict[str, Any]]:
        try:
            return [template.model_dump(by_alias=True) | {"avatarUrl": f"/api/templates/{template.id}/avatar"} for template in registry.list()]
        except TemplateError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.get("/api/templates/{template_id}/avatar")
    def avatar(template_id: str) -> FileResponse:
        try:
            return FileResponse(registry.avatar_path(template_id))
        except TemplateError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/share/contexts")
    def list_share_contexts() -> list[dict[str, str]]:
        try:
            return _share_contexts()
        except ValueError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.get("/api/rooms")
    def list_rooms() -> list[dict[str, Any]]:
        return [room.model_dump() for room in store.list_rooms()]

    @app.post("/api/rooms")
    async def create_room(request: CreateRoomRequest) -> dict[str, Any]:
        try:
            current = store.current_room()
            _stop_room_panes(current.id, "user", "room restart", False, False)
            share_contexts = _validate_share_contexts(request.share_contexts)
            room = store.create_room(
                request.name,
                request.goal,
                request.controller_termination,
                request.agent_termination,
                share_contexts,
            )
            store.add_message(room.id, "user", "user", "User", request.goal, "goal")
            for template_id in request.templates:
                await _deploy(room.id, template_id, 1, "user")
            room = store.get_room(room.id)
            await hub.broadcast(room.id, {"type": "room.created", "room": room.model_dump()})
            return room.model_dump()
        except (ValueError, TemplateError, TmuxError, KeyError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/rooms/reset")
    async def reset_room(request: StopAgentRequest) -> dict[str, Any]:
        try:
            current = store.current_room()
            _stop_room_panes(current.id, request.actor_id, request.reason, request.force, False)
            room = store.reset_room()
            await hub.broadcast(current.id, {"type": "room.reset", "room": room.model_dump()})
            return room.model_dump()
        except (KeyError, TmuxError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/rooms/{room_id}")
    def get_room(room_id: str) -> dict[str, Any]:
        try:
            return store.get_room(room_id).model_dump()
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/rooms/{room_id}/messages")
    def list_messages(room_id: str, after_id: int | None = None) -> list[dict[str, Any]]:
        try:
            return [message.model_dump() for message in store.list_messages(room_id, after_id)]
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/rooms/{room_id}/messages")
    async def post_message(room_id: str, request: PostMessageRequest) -> dict[str, Any]:
        try:
            _assert_can_post(room_id, request)
            message = store.add_message(
                room_id,
                request.actor_type,
                request.actor_id,
                request.actor_name,
                request.text,
                request.kind,
            )
            await hub.broadcast(room_id, {"type": "message.created", "message": message.model_dump()})
            return message.model_dump()
        except (ValueError, KeyError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/rooms/{room_id}/controller/messages")
    def list_controller_messages(room_id: str, after_id: int | None = None) -> list[dict[str, Any]]:
        try:
            return [message.model_dump() for message in store.list_controller_messages(room_id, after_id)]
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/rooms/{room_id}/controller/messages")
    async def post_controller_message(room_id: str, request: PostMessageRequest) -> dict[str, Any]:
        try:
            message = store.add_controller_message(
                room_id,
                request.actor_type,
                request.actor_id,
                request.actor_name,
                request.text,
            )
            extra_messages = []
            if request.actor_type == "user":
                notice = _notify_controller_whisper(room_id, request.text)
                if notice:
                    extra_messages.append(notice)
            await hub.broadcast(room_id, {"type": "controller.message.created", "controller_message": message.model_dump()})
            for extra_message in extra_messages:
                await hub.broadcast(
                    room_id,
                    {"type": "controller.message.created", "controller_message": extra_message.model_dump()},
                )
            return message.model_dump()
        except (ValueError, KeyError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/rooms/{room_id}/events")
    def list_events(room_id: str) -> list[dict[str, Any]]:
        try:
            return [event.model_dump() for event in store.list_events(room_id)]
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/rooms/{room_id}/agents")
    async def deploy_agent(room_id: str, request: DeployAgentRequest) -> dict[str, Any]:
        try:
            room = await _deploy(room_id, request.template_id, request.count, request.actor_id)
            await hub.broadcast(room_id, {"type": "agent.deployed", "room": room.model_dump()})
            return room.model_dump()
        except (TemplateError, TmuxError, KeyError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/rooms/{room_id}/agents/{agent_id}/stop")
    async def stop_agent(room_id: str, agent_id: str, request: StopAgentRequest) -> dict[str, Any]:
        try:
            room = store.get_room(room_id)
            agent = next(agent for agent in room.agents if agent.id == agent_id)
            tmux.stop(agent, request.force)
            room = store.update_agent(
                room_id,
                agent_id,
                {"state": "stopped", "pane_id": None, "reason": request.reason},
                request.actor_id,
                "agent.stopped",
            )
            await hub.broadcast(room_id, {"type": "agent.stopped", "room": room.model_dump()})
            return room.model_dump()
        except (StopIteration, KeyError, TmuxError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/rooms/{room_id}/agents/{agent_id}/done")
    async def mark_agent_done(room_id: str, agent_id: str, request: MarkDoneRequest) -> dict[str, Any]:
        try:
            room = store.update_agent(
                room_id,
                agent_id,
                {"state": "done", "reason": request.reason},
                request.actor_id,
                "agent.done",
            )
            await hub.broadcast(room_id, {"type": "agent.done", "room": room.model_dump()})
            return room.model_dump()
        except KeyError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/rooms/{room_id}/agents/{agent_id}/goal")
    async def set_agent_goal(
        room_id: str,
        agent_id: str,
        request: AgentGoalRequest,
    ) -> dict[str, Any]:
        try:
            room = store.get_room(room_id)
            agent = next(agent for agent in room.agents if agent.id == agent_id)
            tmux.send_goal(
                agent,
                request.goal,
                request.controller_termination,
                request.agent_termination,
            )
            room = store.update_agent(
                room_id,
                agent_id,
                {
                    "state": "active",
                    "goal": request.goal,
                    "controller_termination": (
                        request.controller_termination if agent.template_id == "controller" else None
                    ),
                    "agent_termination": request.agent_termination,
                },
                request.actor_id,
                "agent.goal_set",
            )
            await hub.broadcast(room_id, {"type": "agent.goal_set", "room": room.model_dump()})
            return room.model_dump()
        except (StopIteration, KeyError, TmuxError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/rooms/{room_id}/discussion/close")
    async def close_discussion(room_id: str, request: MarkDoneRequest) -> dict[str, Any]:
        try:
            room = store.set_agent_posting_closed(room_id, True, request.actor_id, request.reason)
            await hub.broadcast(room_id, {"type": "room.discussion_closed", "room": room.model_dump()})
            return room.model_dump()
        except KeyError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/rooms/{room_id}/discussion/open")
    async def open_discussion(room_id: str, request: MarkDoneRequest) -> dict[str, Any]:
        try:
            room = store.set_agent_posting_closed(room_id, False, request.actor_id, request.reason)
            await hub.broadcast(room_id, {"type": "room.discussion_opened", "room": room.model_dump()})
            return room.model_dump()
        except KeyError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/rooms/{room_id}/agents/{agent_id}/mute")
    async def mute_agent(room_id: str, agent_id: str, request: MarkDoneRequest) -> dict[str, Any]:
        try:
            _require_regular_agent(room_id, agent_id)
            room = store.set_agent_muted(room_id, agent_id, True, request.actor_id, request.reason)
            await hub.broadcast(room_id, {"type": "agent.muted", "room": room.model_dump()})
            return room.model_dump()
        except (ValueError, KeyError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/rooms/{room_id}/agents/{agent_id}/unmute")
    async def unmute_agent(room_id: str, agent_id: str, request: MarkDoneRequest) -> dict[str, Any]:
        try:
            _require_regular_agent(room_id, agent_id)
            room = store.set_agent_muted(room_id, agent_id, False, request.actor_id, request.reason)
            await hub.broadcast(room_id, {"type": "agent.unmuted", "room": room.model_dump()})
            return room.model_dump()
        except (ValueError, KeyError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/rooms/{room_id}/agents/{agent_id}/config")
    async def update_agent_config(room_id: str, agent_id: str, request: UpdateAgentConfigRequest) -> dict[str, Any]:
        try:
            room = store.get_room(room_id)
            agent = next(agent for agent in room.agents if agent.id == agent_id)
            if not agent.runtime_dir:
                raise TmuxError(f"agent has no runtime directory: {agent.id}")
            runtime_dir = Path(agent.runtime_dir).resolve()
            target = (runtime_dir / request.relative_path).resolve()
            if runtime_dir not in target.parents and target != runtime_dir:
                raise TmuxError("config path must stay inside the agent runtime directory")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(request.content, encoding="utf-8")
            room = store.update_agent(
                room_id,
                agent_id,
                {"state": agent.state, "config_path": request.relative_path, "reason": request.reason},
                request.actor_id,
                "agent.config_updated",
            )
            await hub.broadcast(room_id, {"type": "agent.config_updated", "room": room.model_dump()})
            return room.model_dump()
        except (StopIteration, KeyError, TmuxError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/rooms/{room_id}/done")
    async def mark_room_done(room_id: str, request: MarkDoneRequest) -> dict[str, Any]:
        try:
            _stop_room_panes(room_id, request.actor_id, request.reason, False, True)
            room = store.set_room_state(room_id, "done", request.actor_id, request.reason)
            await hub.broadcast(room_id, {"type": "room.done", "room": room.model_dump()})
            return room.model_dump()
        except (KeyError, TmuxError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/rooms/{room_id}/stop")
    async def stop_room(room_id: str, request: StopAgentRequest) -> dict[str, Any]:
        try:
            _stop_room_panes(room_id, request.actor_id, request.reason, request.force, False)
            room = store.set_room_state(room_id, "stopped", request.actor_id, request.reason)
            await hub.broadcast(room_id, {"type": "room.stopped", "room": room.model_dump()})
            return room.model_dump()
        except (KeyError, TmuxError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/tmux")
    def tmux_status() -> dict[str, str | bool]:
        return tmux.status()

    @app.websocket("/ws/rooms/{room_id}")
    async def room_ws(websocket: WebSocket, room_id: str) -> None:
        await hub.connect(room_id, websocket)
        try:
            while True:
                await websocket.receive_text()
        except WebSocketDisconnect:
            hub.disconnect(room_id, websocket)

    async def _deploy(room_id: str, template_id: str, count: int, actor_id: str) -> Any:
        room = store.get_room(room_id)
        template = registry.get(template_id)
        template_dir = registry.path_for(template_id)
        for _ in range(count):
            agent = tmux.deploy(
                room,
                template,
                template_dir,
                actor_id,
                room.goal,
                room.controller_termination,
                room.agent_termination,
            )
            room = store.add_agent(room_id, agent, actor_id)
        return room

    def _notify_controller_whisper(room_id: str, text: str) -> Any | None:
        room = store.get_room(room_id)
        controller = next(
            (agent for agent in room.agents if agent.template_id == "controller" and agent.pane_id),
            None,
        )
        if not controller:
            return store.add_controller_message(
                room_id,
                "system",
                "system",
                "System",
                "Controller pane unavailable. The whisper was saved, but no running controller pane received it.",
            )
        try:
            tmux.send_controller_whisper(controller, text)
        except TmuxError as exc:
            return store.add_controller_message(room_id, "system", "system", "System", str(exc))
        return None

    def _stop_room_panes(room_id: str, actor_id: str, reason: str, force: bool, keep_controller: bool) -> None:
        room = store.get_room(room_id)
        for agent in room.agents:
            if keep_controller and agent.template_id == "controller":
                continue
            if not agent.pane_id:
                continue
            tmux.stop(agent, force)
            store.update_agent(
                room_id,
                agent.id,
                {"state": "stopped", "pane_id": None, "reason": reason},
                actor_id,
                "agent.stopped",
            )

    def _assert_can_post(room_id: str, request: PostMessageRequest) -> None:
        if request.actor_type != "agent":
            return
        room = store.get_room(room_id)
        if request.actor_id in room.muted_agent_ids:
            raise ValueError(f"agent is muted: {request.actor_id}")
        if room.agent_posting_closed:
            raise ValueError("room discussion is closed; wait for controller")

    def _require_regular_agent(room_id: str, agent_id: str) -> None:
        room = store.get_room(room_id)
        agent = next((item for item in room.agents if item.id == agent_id), None)
        if not agent:
            raise KeyError(f"agent not found: {agent_id}")
        if agent.template_id == "controller":
            raise ValueError("controller cannot be muted")

    def _share_contexts() -> list[dict[str, str]]:
        if not share_root.is_dir():
            raise ValueError(f"share directory not found: {share_root}")
        contexts = []
        for path in sorted(share_root.iterdir(), key=lambda item: item.name):
            if path.name.startswith("."):
                continue
            if path.is_symlink():
                continue
            if path.is_dir():
                contexts.append({"name": path.name, "path": str(path)})
        return contexts

    def _validate_share_contexts(names: list[str]) -> list[str]:
        if len(names) != len(set(names)):
            raise ValueError("share contexts must be unique")
        available = {context["name"] for context in _share_contexts()}
        for name in names:
            if name not in available:
                raise ValueError(f"share context not found: {name}")
        return names

    return app
