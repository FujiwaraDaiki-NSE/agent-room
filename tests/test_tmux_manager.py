from pathlib import Path

from agent_room.tmux_manager import TmuxManager


def test_link_shared_auth(tmp_path) -> None:
    auth_file = tmp_path / "auth.json"
    auth_file.write_text("{}", encoding="utf-8")
    runtime_dir = tmp_path / "runtime" / "agent"
    (runtime_dir / ".codex").mkdir(parents=True)

    manager = TmuxManager(Path.cwd(), tmp_path, auth_file)
    manager._link_shared_auth(runtime_dir)

    linked = runtime_dir / ".codex" / "auth.json"
    assert linked.is_symlink()
    assert linked.resolve() == auth_file
