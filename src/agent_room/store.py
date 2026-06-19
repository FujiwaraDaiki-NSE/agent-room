from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import AgentInstance, Event, Message, Room


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.path = data_dir / "state.json"
        self.lock = threading.Lock()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self._write({"rooms": {}, "messages": {}, "events": {}, "next_message_id": 1, "next_event_id": 1})

    def list_rooms(self) -> list[Room]:
        with self.lock:
            data = self._read()
            return [Room.model_validate(value) for value in data["rooms"].values()]

    def get_room(self, room_id: str) -> Room:
        with self.lock:
            data = self._read()
            return self._room(data, room_id)

    def create_room(self, name: str, goal: str, termination: str) -> Room:
        self._require("name", name)
        self._require("goal", goal)
        self._require("termination", termination)
        room_id = f"room-{uuid.uuid4().hex[:8]}"
        room = Room(
            id=room_id,
            name=name,
            goal=goal,
            termination=termination,
            state="open",
            created_at=now_iso(),
            agents=[],
        )
        with self.lock:
            data = self._read()
            data["rooms"][room_id] = room.model_dump()
            data["messages"][room_id] = []
            data["events"][room_id] = []
            self._append_event(data, room_id, "room.created", "system", None, None, {"name": name})
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
        return json.loads(self.path.read_text(encoding="utf-8"))

    def _write(self, data: dict[str, Any]) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def _require(self, name: str, value: str) -> None:
        if not value.strip():
            raise ValueError(f"{name} is required")
