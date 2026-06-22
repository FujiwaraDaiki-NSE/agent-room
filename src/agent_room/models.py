from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


AgentState = Literal["starting", "active", "idle", "speaking", "done", "stopped", "failed"]
RoomState = Literal["draft", "starting", "open", "done", "stopped"]
ActorType = Literal["user", "agent", "controller", "system"]


class AgentTemplate(BaseModel):
    id: str
    name: str
    short_name: str = Field(alias="shortName")
    role: str
    personality: str
    accent: str
    avatar: str
    scope: Literal["controller", "agent"]
    summary: str
    launch: bool
    permissions: list[str]


class AgentTeam(BaseModel):
    id: str
    name: str
    summary: str
    templates: list[str]


class AgentInstance(BaseModel):
    id: str
    room_id: str
    template_id: str
    name: str
    short_name: str
    role: str
    personality: str
    accent: str
    avatar_url: str
    state: AgentState
    pane_id: str | None = None
    runtime_dir: str | None = None
    goal: str | None = None
    controller_termination: str | None = None
    agent_termination: str | None = None
    codex_session_id: str | None = None
    config_path: str | None = None
    reason: str | None = None


class Message(BaseModel):
    id: int
    room_id: str
    actor_type: ActorType
    actor_id: str
    actor_name: str
    text: str
    kind: Literal["message", "facilitation", "goal", "state"] = "message"
    created_at: str


class Event(BaseModel):
    id: int
    room_id: str
    type: str
    actor_id: str
    target_id: str | None
    reason: str | None
    payload: dict[str, Any]
    created_at: str


class MeetingStatus(BaseModel):
    phase: str
    topic: str
    summary: str
    decisions: list[str]
    open_questions: list[str]
    next: str
    updated_at: str | None


class Room(BaseModel):
    id: str
    name: str
    goal: str
    controller_termination: str
    agent_termination: str
    share_contexts: list[str]
    agent_posting_closed: bool
    muted_agent_ids: list[str]
    state: RoomState
    created_at: str
    meeting_status: MeetingStatus
    agents: list[AgentInstance]


class CreateRoomRequest(BaseModel):
    name: str
    goal: str
    controller_termination: str
    agent_termination: str
    share_contexts: list[str]
    templates: list[str]
    teams: list[str]


class PostMessageRequest(BaseModel):
    actor_type: ActorType
    actor_id: str
    actor_name: str
    text: str
    kind: Literal["message", "facilitation", "goal", "state"]


class DeployAgentRequest(BaseModel):
    template_id: str
    count: int = Field(ge=1, le=12)
    actor_id: str


class StopAgentRequest(BaseModel):
    actor_id: str
    reason: str
    force: bool


class MarkDoneRequest(BaseModel):
    actor_id: str
    reason: str


class AgentGoalRequest(BaseModel):
    actor_id: str
    goal: str
    controller_termination: str
    agent_termination: str


class UpdateAgentConfigRequest(BaseModel):
    actor_id: str
    relative_path: str
    content: str
    reason: str


class UpdateMeetingStatusRequest(BaseModel):
    actor_id: str
    phase: str
    topic: str
    summary: str
    decisions: list[str]
    open_questions: list[str]
    next: str
