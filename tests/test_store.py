from agent_room.store import Store


def test_room_message_and_done_flow(tmp_path) -> None:
    store = Store(tmp_path)
    assert len(store.list_rooms()) == 1

    room = store.create_room("Spec", "Discuss the design", "All agents done")

    message = store.add_message(room.id, "user", "user", "User", "Start", "goal")
    assert message.id == 1

    messages = store.list_messages(room.id, after_id=None)
    assert [item.text for item in messages] == ["Start"]

    done = store.set_room_state(room.id, "done", "controller", "complete")
    assert done.state == "done"
    assert store.list_events(room.id)[-1].type == "room.done"


def test_reset_keeps_single_room(tmp_path) -> None:
    store = Store(tmp_path)
    first = store.current_room()
    store.create_room("Spec", "Discuss", "Done")

    second = store.reset_room()

    assert first.id != second.id
    assert second.state == "draft"
    assert store.list_rooms() == [second]


def test_controller_messages_are_separate_from_room_messages(tmp_path) -> None:
    store = Store(tmp_path)
    room = store.create_room("Spec", "Discuss", "Done")

    store.add_message(room.id, "user", "user", "User", "Public", "message")
    private = store.add_controller_message(room.id, "user", "user", "User", "Private")

    assert [message.text for message in store.list_messages(room.id, None)] == ["Public"]
    assert [message.text for message in store.list_controller_messages(room.id, None)] == [private.text]
