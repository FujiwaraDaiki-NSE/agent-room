from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import AgentInstance, ActorType, Event, Message, Room


AUTO_ROOM_NAME = "Room"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.path = data_dir / "state.json"
        self.lock = threading.Lock()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self._write(self._empty_data())
        self.ensure_current_room()

    def list_rooms(self) -> list[Room]:
        return [self.current_room()]

    def current_room(self) -> Room:
        with self.lock:
            data = self._read()
            room_id = self._current_room_id(data)
            return self._room(data, room_id)

    def get_room(self, room_id: str) -> Room:
        with self.lock:
            data = self._read()
            return self._room(data, room_id)

    def ensure_current_room(self) -> Room:
        with self.lock:
            data = self._read()
            room_id = data["current_room_id"]
            if room_id and room_id in data["rooms"]:
                return self._room(data, room_id)
            room = self._new_room(AUTO_ROOM_NAME, "", "", "", "draft")
            data["rooms"] = {room.id: room.model_dump()}
            data["messages"] = {room.id: []}
            data["controller_messages"] = {room.id: []}
            data["events"] = {room.id: []}
            data["current_room_id"] = room.id
            self._append_event(data, room.id, "room.created", "system", None, None, {"name": room.name})
            self._write(data)
            return room

    def reset_room(self) -> Room:
        with self.lock:
            data = self._read()
            room = self._new_room(AUTO_ROOM_NAME, "", "", "", "draft")
            data["rooms"] = {room.id: room.model_dump()}
            data["messages"] = {room.id: []}
            data["controller_messages"] = {room.id: []}
            data["events"] = {room.id: []}
            data["current_room_id"] = room.id
            self._append_event(data, room.id, "room.created", "system", None, None, {"name": room.name})
            self._write(data)
            return room

    def create_room(
        self,
        name: str,
        goal: str,
        controller_termination: str,
        agent_termination: str,
    ) -> Room:
        self._require("name", name)
        self._require("goal", goal)
        self._require("controller_termination", controller_termination)
        self._require("agent_termination", agent_termination)
        with self.lock:
            data = self._read()
            room_id = self._current_room_id(data)
            room = self._room(data, room_id)
            room.name = name
            room.goal = goal
            room.controller_termination = controller_termination
            room.agent_termination = agent_termination
            room.state = "open"
            room.agents = []
            data["rooms"] = {room.id: room.model_dump()}
            data["messages"] = {room.id: []}
            data["controller_messages"] = {room.id: []}
            data["events"] = {room.id: []}
            data["current_room_id"] = room.id
            self._append_event(data, room.id, "room.started", "system", None, None, {"name": name})
            self._write(data)
        return room

    def add_agent(self, room_id: str, agent: AgentInstance, actor_id: str) -> Room:
        with self.lock:
            data = self._read()
            room = self._room(data, room_id)
            room.agents.append(agent)
            data["rooms"][room_id] = room.model_dump()
            self._append_event(data, room_id, "agent.deployed", actor_id, agent.id, None, agent.model_dump())
            self._write(data)
            return room

    def update_agent(self, room_id: str, agent_id: str, patch: dict[str, Any], actor_id: str, event_type: str) -> Room:
        with self.lock:
            data = self._read()
            room = self._room(data, room_id)
            updated = False
            agents: list[AgentInstance] = []
            for agent in room.agents:
                if agent.id == agent_id:
                    for key, value in patch.items():
                        setattr(agent, key, value)
                    updated = True
                agents.append(agent)
            if not updated:
                raise KeyError(f"agent not found: {agent_id}")
            room.agents = agents
            data["rooms"][room_id] = room.model_dump()
            self._append_event(data, room_id, event_type, actor_id, agent_id, patch.get("reason"), patch)
            self._write(data)
            return room

    def set_room_state(self, room_id: str, state: str, actor_id: str, reason: str) -> Room:
        with self.lock:
            data = self._read()
            room = self._room(data, room_id)
            room.state = state  # type: ignore[assignment]
            data["rooms"][room_id] = room.model_dump()
            self._append_event(data, room_id, f"room.{state}", actor_id, room_id, reason, {})
            self._write(data)
            return room

    def add_message(
        self,
        room_id: str,
        actor_type: str,
        actor_id: str,
        actor_name: str,
        text: str,
        kind: str,
    ) -> Message:
        self._require("actor_id", actor_id)
        self._require("actor_name", actor_name)
        self._require("text", text)
        with self.lock:
            data = self._read()
            self._room(data, room_id)
            message_id = int(data["next_message_id"])
            data["next_message_id"] = message_id + 1
            message = Message(
                id=message_id,
                room_id=room_id,
                actor_type=actor_type,  # type: ignore[arg-type]
                actor_id=actor_id,
                actor_name=actor_name,
                text=text,
                kind=kind,  # type: ignore[arg-type]
                created_at=now_iso(),
            )
            data["messages"][room_id].append(message.model_dump())
            self._append_event(data, room_id, "message.created", actor_id, None, None, {"message_id": message_id})
            self._write(data)
            return message

    def list_messages(self, room_id: str, after_id: int | None) -> list[Message]:
        with self.lock:
            data = self._read()
            self._room(data, room_id)
            messages = [Message.model_validate(item) for item in data["messages"][room_id]]
        if after_id is None:
            return messages
        return [message for message in messages if message.id > after_id]

    def add_controller_message(
        self,
        room_id: str,
        actor_type: ActorType,
        actor_id: str,
        actor_name: str,
        text: str,
    ) -> Message:
        self._require("actor_id", actor_id)
        self._require("actor_name", actor_name)
        self._require("text", text)
        with self.lock:
            data = self._read()
            self._room(data, room_id)
            message_id = int(data["next_message_id"])
            data["next_message_id"] = message_id + 1
            message = Message(
                id=message_id,
                room_id=room_id,
                actor_type=actor_type,
                actor_id=actor_id,
                actor_name=actor_name,
                text=text,
                kind="message",
                created_at=now_iso(),
            )
            data["controller_messages"][room_id].append(message.model_dump())
            self._append_event(data, room_id, "controller.message.created", actor_id, None, None, {"message_id": message_id})
            self._write(data)
            return message

    def list_controller_messages(self, room_id: str, after_id: int | None) -> list[Message]:
        with self.lock:
            data = self._read()
            self._room(data, room_id)
            messages = [Message.model_validate(item) for item in data["controller_messages"][room_id]]
        if after_id is None:
            return messages
        return [message for message in messages if message.id > after_id]

    def list_events(self, room_id: str) -> list[Event]:
        with self.lock:
            data = self._read()
            self._room(data, room_id)
            return [Event.model_validate(item) for item in data["events"][room_id]]

    def _append_event(
        self,
        data: dict[str, Any],
        room_id: str,
        event_type: str,
        actor_id: str,
        target_id: str | None,
        reason: str | None,
        payload: dict[str, Any],
    ) -> Event:
        event_id = int(data["next_event_id"])
        data["next_event_id"] = event_id + 1
        event = Event(
            id=event_id,
            room_id=room_id,
            type=event_type,
            actor_id=actor_id,
            target_id=target_id,
            reason=reason,
            payload=payload,
            created_at=now_iso(),
        )
        data["events"][room_id].append(event.model_dump())
        return event

    def _room(self, data: dict[str, Any], room_id: str) -> Room:
        if room_id not in data["rooms"]:
            raise KeyError(f"room not found: {room_id}")
        return Room.model_validate(data["rooms"][room_id])

    def _read(self) -> dict[str, Any]:
        return self._normalize(json.loads(self.path.read_text(encoding="utf-8")))

    def _write(self, data: dict[str, Any]) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def _require(self, name: str, value: str) -> None:
        if not value.strip():
            raise ValueError(f"{name} is required")

    def _empty_data(self) -> dict[str, Any]:
        return {
            "rooms": {},
            "messages": {},
            "controller_messages": {},
            "events": {},
            "current_room_id": None,
            "next_message_id": 1,
            "next_event_id": 1,
        }

    def _normalize(self, data: dict[str, Any]) -> dict[str, Any]:
        if "controller_messages" not in data:
            data["controller_messages"] = {}
        if "current_room_id" not in data:
            rooms = list(data["rooms"].keys())
            data["current_room_id"] = rooms[-1] if rooms else None
        for room_id in data["rooms"].keys():
            room = data["rooms"][room_id]
            if "controller_termination" not in room:
                room["controller_termination"] = room.get("termination", "")
            if "agent_termination" not in room:
                room["agent_termination"] = room.get("termination", "")
            if room_id not in data["messages"]:
                data["messages"][room_id] = []
            if room_id not in data["controller_messages"]:
                data["controller_messages"][room_id] = []
            if room_id not in data["events"]:
                data["events"][room_id] = []
        return data

    def _current_room_id(self, data: dict[str, Any]) -> str:
        room_id = data["current_room_id"]
        if not room_id or room_id not in data["rooms"]:
            raise KeyError("current room not found")
        return room_id

    def _new_room(
        self,
        name: str,
        goal: str,
        controller_termination: str,
        agent_termination: str,
        state: str,
    ) -> Room:
        room_id = f"room-{uuid.uuid4().hex[:8]}"
        return Room(
            id=room_id,
            name=name,
            goal=goal,
            controller_termination=controller_termination,
            agent_termination=agent_termination,
            state=state,  # type: ignore[arg-type]
            created_at=now_iso(),
            agents=[],
        )
