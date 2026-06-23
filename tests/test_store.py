import json

from agent_room.store import Store


def test_room_message_and_done_flow(tmp_path) -> None:
    store = Store(tmp_path)
    assert len(store.list_rooms()) == 1

    room = store.create_room("Spec", "Discuss the design", "Controller done", "Agents done", [], [], "open")
    assert room.meeting_status.phase == "Open"
    assert room.meeting_status.topic == "Discuss the design"
    assert room.planned_template_ids == []
    assert room.agent_posting_closed is True

    message = store.add_message(room.id, "user", "user", "User", "Start", "goal")
    assert message.id == 1

    messages = store.list_messages(room.id, after_id=None)
    assert [item.text for item in messages] == ["Start"]

    done = store.set_room_state(room.id, "done", "controller", "complete")
    assert done.state == "done"
    assert store.list_events(room.id)[-1].type == "room.done"


def test_update_meeting_status_persists_status(tmp_path) -> None:
    store = Store(tmp_path)
    room = store.create_room("Spec", "Discuss", "Controller done", "Agents done", [], [], "open")

    updated = store.update_meeting_status(
        room.id,
        "controller-1",
        "Synthesis",
        "Evidence registry",
        "Converging on traceability.",
        ["Use registry"],
        ["Owner granularity"],
        "Ask final objections",
    )

    assert updated.meeting_status.phase == "Synthesis"
    assert updated.meeting_status.decisions == ["Use registry"]
    assert updated.meeting_status.open_questions == ["Owner granularity"]
    assert updated.meeting_status.updated_at
    assert store.current_room().meeting_status.summary == "Converging on traceability."
    assert store.list_events(room.id)[-1].type == "room.status_updated"


def test_reset_keeps_single_room(tmp_path) -> None:
    store = Store(tmp_path)
    first = store.current_room()
    store.create_room("Spec", "Discuss", "Controller done", "Agents done", [], [], "open")

    second = store.reset_room()

    assert first.id != second.id
    assert second.state == "draft"
    assert store.list_rooms() == [second]


def test_controller_messages_are_separate_from_room_messages(tmp_path) -> None:
    store = Store(tmp_path)
    room = store.create_room("Spec", "Discuss", "Controller done", "Agents done", [], [], "open")

    store.add_message(room.id, "user", "user", "User", "Public", "message")
    private = store.add_controller_message(room.id, "user", "user", "User", "Private")

    assert [message.text for message in store.list_messages(room.id, None)] == ["Public"]
    assert [message.text for message in store.list_controller_messages(room.id, None)] == [private.text]


def test_discussion_close_is_room_state(tmp_path) -> None:
    store = Store(tmp_path)
    room = store.create_room("Spec", "Discuss", "Controller done", "Agents done", [], [], "open")

    closed = store.set_agent_posting_closed(room.id, True, "controller-1", "final report")

    assert closed.agent_posting_closed is True
    assert store.list_events(room.id)[-1].type == "room.discussion_closed"


def test_old_termination_state_is_migrated(tmp_path) -> None:
    state = {
        "rooms": {
            "room-old": {
                "id": "room-old",
                "name": "Old",
                "goal": "Discuss",
                "termination": "Done",
                "state": "open",
                "created_at": "2026-06-19T00:00:00+00:00",
                "agents": [],
            }
        },
        "messages": {"room-old": []},
        "events": {"room-old": []},
        "current_room_id": "room-old",
        "next_message_id": 1,
        "next_event_id": 1,
    }
    (tmp_path / "state.json").write_text(json.dumps(state), encoding="utf-8")

    room = Store(tmp_path).current_room()

    assert room.controller_termination == "Done"
    assert room.agent_termination == "Done"
    assert room.share_contexts == []
    assert room.planned_template_ids == []
    assert room.agent_posting_closed is False
    assert room.muted_agent_ids == []
    assert room.meeting_status.phase == "Open"
    assert room.meeting_status.topic == "Discuss"
