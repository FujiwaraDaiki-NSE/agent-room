from agent_room.store import Store


def test_room_message_and_done_flow(tmp_path) -> None:
    store = Store(tmp_path)
    room = store.create_room("Spec", "Discuss the design", "All agents done")

    message = store.add_message(room.id, "user", "user", "User", "Start", "goal")
    assert message.id == 1

    messages = store.list_messages(room.id, after_id=None)
    assert [item.text for item in messages] == ["Start"]

    done = store.set_room_state(room.id, "done", "controller", "complete")
    assert done.state == "done"
    assert store.list_events(room.id)[-1].type == "room.done"
