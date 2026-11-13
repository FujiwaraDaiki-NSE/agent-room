from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import SeatRequest, Whisper, Sticky, WhiteboardVersion, Seat, initial_seats, AgentInstance, ActorType, Event, FloorRequest, MeetingStatus, Message, Room, RoomState


AUTO_ROOM_NAME = "Room"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def meeting_status(phase: str, topic: str, summary: str, next_step: str, updated_at: str | None) -> MeetingStatus:
    return MeetingStatus(
        phase=phase,
        topic=topic,
        summary=summary,
        decisions=[],
        open_questions=[],
        next=next_step,
        updated_at=updated_at,
    )


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
        share_contexts: list[str],
        planned_template_ids: list[str],
        state: RoomState,
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
            room.share_contexts = share_contexts
            room.planned_template_ids = planned_template_ids
            room.agent_posting_closed = True
            room.muted_agent_ids = []
            room.floor_grants = []
            room.floor_requests = []
            room.state = state
            room.meeting_status = meeting_status(self._room_state_label(state), goal, "", "Controller", None)
            room.seats = initial_seats()
            room.seat_requests = []
            room.whispers = []
            room.stickies = []
            room.minutes = ""
            room.whiteboard = []
            room.agents = []
            data["rooms"] = {room.id: room.model_dump()}
            data["messages"] = {room.id: []}
            data["controller_messages"] = {room.id: []}
            data["events"] = {room.id: []}
            data["current_room_id"] = room.id
            self._append_event(
                data,
                room.id,
                self._room_state_event_type(state),
                "system",
                None,
                None,
                {
                    "name": name,
                    "share_contexts": share_contexts,
                    "planned_template_ids": planned_template_ids,
                },
            )
            self._write(data)
        return room

    def update_share(self, room_id: str, paths: list[str]) -> Room:
        with self.lock:
            data = self._read()
            room = self._room(data, room_id)
            room.share_contexts = paths
            data["rooms"][room_id] = room.model_dump()
            self._append_event(data, room_id, "share.updated", "user", None, None, {"paths": paths})
            self._write(data)
            return room

    def record_share_access(self, room_id: str, actor_id: str, path: str, operation: str) -> None:
        with self.lock:
            data = self._read()
            room = self._room(data, room_id)
            if not any(agent.id == actor_id for agent in room.agents):
                raise ValueError("agent not found")
            self._append_event(data, room_id, "share.accessed", actor_id, path, None, {"path": path, "operation": operation})
            self._write(data)

    def update_meeting_status(
        self,
        room_id: str,
        actor_id: str,
        phase: str,
        topic: str,
        summary: str,
        decisions: list[str],
        open_questions: list[str],
        next_step: str,
    ) -> Room:
        self._require("actor_id", actor_id)
        self._require("phase", phase)
        self._require("topic", topic)
        self._require("summary", summary)
        self._require("next", next_step)
        with self.lock:
            data = self._read()
            room = self._room(data, room_id)
            room.meeting_status = MeetingStatus(
                phase=phase,
                topic=topic,
                summary=summary,
                decisions=decisions,
                open_questions=open_questions,
                next=next_step,
                updated_at=now_iso(),
            )
            data["rooms"][room_id] = room.model_dump()
            self._append_event(
                data,
                room_id,
                "room.status_updated",
                actor_id,
                room_id,
                None,
                room.meeting_status.model_dump(),
            )
            self._write(data)
            return room

    def set_agent_posting_closed(self, room_id: str, closed: bool, actor_id: str, reason: str) -> Room:
        with self.lock:
            data = self._read()
            room = self._room(data, room_id)
            room.agent_posting_closed = closed
            data["rooms"][room_id] = room.model_dump()
            event_type = "room.discussion_closed" if closed else "room.discussion_opened"
            self._append_event(data, room_id, event_type, actor_id, room_id, reason, {"closed": closed})
            self._write(data)
            return room

    def set_agent_muted(self, room_id: str, agent_id: str, muted: bool, actor_id: str, reason: str) -> Room:
        with self.lock:
            data = self._read()
            room = self._room(data, room_id)
            if not any(agent.id == agent_id for agent in room.agents):
                raise KeyError(f"agent not found: {agent_id}")
            muted_ids = set(room.muted_agent_ids)
            if muted:
                muted_ids.add(agent_id)
            else:
                muted_ids.discard(agent_id)
            room.muted_agent_ids = sorted(muted_ids)
            data["rooms"][room_id] = room.model_dump()
            event_type = "agent.muted" if muted else "agent.unmuted"
            self._append_event(
                data,
                room_id,
                event_type,
                actor_id,
                agent_id,
                reason,
                {"muted": muted, "reason": reason},
            )
            self._write(data)
            return room

    def add_agent(self, room_id: str, agent: AgentInstance, actor_id: str) -> Room:
        with self.lock:
            data = self._read()
            room = self._room(data, room_id)
            if agent.template_id != "controller":
                seat = self._audience_seat(room)
                seat.agent_id = agent.id
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
            if patch.get("state") == "stopped":
                for seat in room.seats:
                    if seat.agent_id == agent_id:
                        seat.agent_id = None
                room.seat_requests = [r for r in room.seat_requests if r.agent_id != agent_id]
                room.floor_grants = [a for a in room.floor_grants if a != agent_id]
            data["rooms"][room_id] = room.model_dump()
            self._append_event(data, room_id, event_type, actor_id, agent_id, patch.get("reason"), patch)
            self._write(data)
            return room

    def set_room_state(self, room_id: str, state: RoomState, actor_id: str, reason: str) -> Room:
        with self.lock:
            data = self._read()
            room = self._room(data, room_id)
            room.state = state
            if room.meeting_status.updated_at is None:
                room.meeting_status.phase = self._room_state_label(state)
                if not room.meeting_status.topic:
                    room.meeting_status.topic = room.goal
                if state == "open":
                    room.meeting_status.next = "Controller"
            data["rooms"][room_id] = room.model_dump()
            self._append_event(data, room_id, self._room_state_event_type(state), actor_id, room_id, reason, {})
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
            room = self._room(data, room_id)
            agent = next((a for a in room.agents if a.id == actor_id), None)
            if actor_type == "agent" or (agent is not None and agent.template_id != "controller"):
                if agent is None:
                    raise ValueError(f"agent not found: {actor_id}")
                self._seat_tool(room, actor_id, "room_post")
                if actor_id in room.muted_agent_ids:
                    raise ValueError(f"agent is muted: {actor_id}")
                if room.agent_posting_closed:
                    raise ValueError("room discussion is closed; wait for controller")
                if actor_id not in room.floor_grants:
                    raise ValueError("agent has no floor; use floor_request")
                room.floor_grants.remove(actor_id)
            if text.startswith("宛先: "):
                target = text.split("\n", 1)[0][len("宛先: "):].strip()
                matches = [a for a in room.agents if a.template_id != "controller" and a.id != actor_id and target in (a.name, a.id)]
                if len(matches) == 1 and matches[0].id not in room.floor_grants:
                    room.floor_grants.append(matches[0].id)
            data["rooms"][room_id] = room.model_dump()
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

    def grant_floor(self, room_id: str, agent_ids: list[str]) -> Room:
        with self.lock:
            data = self._read()
            room = self._room(data, room_id)
            for agent_id in agent_ids:
                if agent_id not in room.floor_grants:
                    room.floor_grants.append(agent_id)
            data["rooms"][room_id] = room.model_dump()
            self._write(data)
            return room

    def request_floor(self, room_id: str, agent_id: str, kind: str, target: str, summary: str) -> Room:
        self._require("target", target)
        self._require("summary", summary)
        if len(summary.splitlines()) != 1:
            raise ValueError("summary must be one line")
        with self.lock:
            data = self._read()
            room = self._room(data, room_id)
            self._seat_tool(room, agent_id, "floor_request")
            if any(r.agent_id == agent_id for r in room.floor_requests):
                raise ValueError("agent already has an unresolved floor request")
            room.floor_requests.append(FloorRequest(
                id=uuid.uuid4().hex, agent_id=agent_id, kind=kind,
                target=target, summary=summary, status="pending", reason=None,
            ))
            data["rooms"][room_id] = room.model_dump()
            self._write(data)
            return room

    def resolve_floor(self, room_id: str, request_id: str, approve: bool, reason: str | None, defer: bool) -> Room:
        with self.lock:
            data = self._read()
            room = self._room(data, room_id)
            request = next((r for r in room.floor_requests if r.id == request_id), None)
            if request is None:
                raise ValueError(f"floor request not found: {request_id}")
            if approve and request.agent_id not in room.floor_grants:
                room.floor_grants.append(request.agent_id)
            if defer:
                request.status = "deferred"
                request.reason = reason
            else:
                room.floor_requests.remove(request)
            data["rooms"][room_id] = room.model_dump()
            self._write(data)
            return room

    def _audience_seat(self, room: Room) -> Seat:
        seat = next((s for s in room.seats if s.kind == "audience" and s.agent_id is None), None)
        if seat is None:
            raise ValueError("audience seats full")
        return seat

    def _seat_tool(self, room: Room, agent_id: str, tool: str) -> Seat:
        seat = next((s for s in room.seats if s.agent_id == agent_id), None)
        allowed = {"room_post": {"podium", "discussion"}, "floor_request": {"podium", "discussion"},
                   "seat_whisper": {"discussion", "audience"}, "minutes_update": {"scribe"},
                   "sticky_post": {"podium", "discussion", "scribe", "audience"}}
        if seat is None or (tool in allowed and seat.kind not in allowed[tool]):
            raise ValueError(f"seat does not allow {tool}")
        return seat

    def seat_action(self, room_id: str, agent_id: str, action: str, seat_id: str | None, reason: str | None) -> Room:
        with self.lock:
            data = self._read()
            room = self._room(data, room_id)
            current = self._seat_tool(room, agent_id, action)
            if action == "leave":
                if current.kind == "audience":
                    raise ValueError("already in audience")
                target = self._audience_seat(room)
            else:
                target = next((s for s in room.seats if s.id == seat_id), None)
                if target is None:
                    raise ValueError("seat not found")
            if target.agent_id is not None:
                raise ValueError("seat occupied")
            if action == "move" and target.kind != current.kind:
                raise ValueError("different seat kind requires approval")
            if action == "request":
                if target.kind == current.kind:
                    raise ValueError("same kind uses seat_move")
                if reason is None:
                    raise ValueError("reason is required")
                self._require("reason", reason)
                if any(r.agent_id == agent_id for r in room.seat_requests):
                    raise ValueError("agent already has an unresolved seat request")
                room.seat_requests.append(SeatRequest(id=uuid.uuid4().hex, agent_id=agent_id, seat_id=target.id, reason=reason))
            else:
                current.agent_id = None
                target.agent_id = agent_id
                if action != "move":
                    room.seat_requests = [r for r in room.seat_requests if r.agent_id != agent_id]
                if target.kind == "podium" and agent_id not in room.floor_grants:
                    room.floor_grants.append(agent_id)
            data["rooms"][room_id] = room.model_dump()
            self._write(data)
            return room

    def decline_seat(self, room_id: str, request_id: str, reason: str) -> Room:
        self._require("reason", reason)
        with self.lock:
            data = self._read()
            room = self._room(data, room_id)
            entry = next((r for r in room.seat_requests if r.id == request_id), None)
            if entry is None:
                raise ValueError("seat request not found")
            room.seat_requests.remove(entry)
            data["rooms"][room_id] = room.model_dump()
            self._write(data)
            return room

    def whisper(self, room_id: str, agent_id: str, to_agent_id: str, text: str) -> Room:
        if not 1 <= len(text) <= 200:
            raise ValueError("whisper text must be 1–200 characters")
        with self.lock:
            data = self._read()
            room = self._room(data, room_id)
            sender = self._seat_tool(room, agent_id, "seat_whisper")
            target = self._seat_tool(room, to_agent_id, "seat_whisper")
            distance = abs(sender.index - target.index)
            adjacent = sender.kind == target.kind and ((sender.kind == "discussion" and distance in (1, 7)) or
                       (sender.kind == "audience" and distance == 1 and (sender.index - 1) // 6 == (target.index - 1) // 6))
            if not adjacent:
                raise ValueError("whispers require adjacent seats")
            last = next((w for w in reversed(room.whispers) if {w.from_agent_id, w.to_agent_id} == {agent_id, to_agent_id}), None)
            if last is not None and last.from_agent_id == agent_id:
                raise ValueError("wait for recipient reply")
            room.whispers.append(Whisper(id=uuid.uuid4().hex, from_agent_id=agent_id, to_agent_id=to_agent_id, text=text, created_at=now_iso()))
            data["rooms"][room_id] = room.model_dump()
            self._write(data)
            return room

    def update_board(self, room_id: str, agent_id: str, action: str, text: str, title: str | None) -> Room:
        with self.lock:
            data = self._read()
            room = self._room(data, room_id)
            if action == "sticky":
                self._seat_tool(room, agent_id, "sticky_post")
                if not 1 <= len(text) <= 80:
                    raise ValueError("sticky text must be 1–80 characters")
                room.stickies.append(Sticky(id=uuid.uuid4().hex, agent_id=agent_id, text=text, created_at=now_iso()))
            elif action == "minutes":
                self._seat_tool(room, agent_id, "minutes_update")
                room.minutes = text
            elif action == "clear":
                room.stickies = []
            elif action == "whiteboard":
                if title is None:
                    raise ValueError("title must be a string")
                room.whiteboard.append(WhiteboardVersion(id=uuid.uuid4().hex, title=title, html=text, phase=room.meeting_status.phase, created_at=now_iso()))
            data["rooms"][room_id] = room.model_dump()
            self._write(data)
            return room

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
        room = Room.model_validate(data["rooms"][room_id])
        room.floor_grants = list(dict.fromkeys(room.floor_grants))
        return room

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
            if "seats" not in room:
                room.update(seats=[s.model_dump() for s in initial_seats()], seat_requests=[], whispers=[], stickies=[], minutes="", whiteboard=[])
                regular = [a for a in room["agents"] if a["template_id"] != "controller" and a["state"] != "stopped"]
                if len(regular) > 12:
                    raise ValueError("cannot migrate room: audience seats full")
                for seat, agent in zip([s for s in room["seats"] if s["kind"] == "audience"], regular):
                    seat["agent_id"] = agent["id"]
            if "controller_termination" not in room:
                room["controller_termination"] = room.get("termination", "")
            if "agent_termination" not in room:
                room["agent_termination"] = room.get("termination", "")
            if "share_contexts" not in room:
                room["share_contexts"] = []
            if "planned_template_ids" not in room:
                room["planned_template_ids"] = []
            if "agent_posting_closed" not in room:
                room["agent_posting_closed"] = False
            if "floor_grants" not in room:
                room["floor_grants"] = []
                room["floor_requests"] = []
            if "muted_agent_ids" not in room:
                room["muted_agent_ids"] = []
            if "meeting_status" not in room:
                room["meeting_status"] = meeting_status(
                    self._room_state_label(room.get("state", "draft")),
                    room.get("goal", ""),
                    "",
                    "Controller" if room.get("state") == "open" else "",
                    None,
                ).model_dump()
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
        state: RoomState,
    ) -> Room:
        room_id = f"room-{uuid.uuid4().hex[:8]}"
        return Room(
            id=room_id,
            name=name,
            goal=goal,
            controller_termination=controller_termination,
            agent_termination=agent_termination,
            share_contexts=[],
            planned_template_ids=[],
            agent_posting_closed=False,
            muted_agent_ids=[],
            floor_grants=[],
            floor_requests=[],
            state=state,
            created_at=now_iso(),
            meeting_status=meeting_status(self._room_state_label(state), goal, "", "", None),
            agents=[], seats=initial_seats(), seat_requests=[], whispers=[], stickies=[], minutes="", whiteboard=[],
        )

    def _room_state_event_type(self, state: RoomState) -> str:
        if state == "open":
            return "room.started"
        return f"room.{state}"

    def _room_state_label(self, state: str) -> str:
        return state[:1].upper() + state[1:]
