from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent_room.share import validate_paths, list_path, read_path
from agent_room.server import create_app
from agent_room.tmux_manager import TmuxManager
from test_server import agent_instance, write_room_agents


def test_paths_and_excluded_directories(tmp_path):
    (tmp_path / 'docs').mkdir()
    (tmp_path / 'docs/spec.md').write_text('仕様')
    (tmp_path / 'docs/.git').mkdir()
    (tmp_path / 'node_modules').mkdir()
    (tmp_path / 'escape').symlink_to(tmp_path.parent)
    for paths in [['missing'], ['../outside'], ['/tmp'], ['docs', 'docs/spec.md'], ['docs', 'docs'], ['docs/.git'], ['escape']]:
        with pytest.raises(ValueError):
            validate_paths(tmp_path, paths)
    assert validate_paths(tmp_path, ['docs/spec.md']) == ['docs/spec.md']
    assert [p['name'] for p in list_path(tmp_path)] == ['docs']
    assert [p['name'] for p in list_path(tmp_path, 'docs')] == ['spec.md']


def test_update_links_notify_and_access_history(tmp_path, monkeypatch):
    project = tmp_path / 'project'
    (project / 'share/docs').mkdir(parents=True)
    (project / 'share/docs/spec.md').write_text('仕様')
    (project / 'share/other.txt').write_text('非公開')
    auth = tmp_path / 'auth.json'; auth.write_text('{}')
    data = tmp_path / 'data'
    client = TestClient(create_app(project, data, auth))
    room = client.get('/api/rooms').json()[0]
    runtime = tmp_path / 'runtime'; runtime.mkdir()
    (runtime / 'share').mkdir()
    (runtime / 'share/local.txt').write_text('keep')
    (runtime / 'share/local-empty').mkdir()
    agent = agent_instance('reader', 'builder', 'active', '%1', str(runtime))
    write_room_agents(data, room['id'], [agent])
    notices = []
    monkeypatch.setattr(TmuxManager, '_send_codex_prompt', lambda self, pane, text: notices.append((pane, text)))
    base = f"/api/rooms/{room['id']}"
    assert client.put(base + '/share', json={'paths':['docs/spec.md']}).status_code == 200
    assert (runtime / 'share/docs').is_dir() and not (runtime / 'share/docs').is_symlink()
    assert (runtime / 'share/docs/spec.md').is_symlink()
    assert client.get(base + '/share/content', params={'path':'docs/spec.md'}).json()['content'] == '仕様'
    assert client.get(base + '/share/content', params={'path':'other.txt'}).status_code == 400
    assert client.post(base + '/share/access', json={'actor_id':'reader','path':'docs/spec.md','operation':'read'}).status_code == 200
    event = client.get(base + '/events').json()[-1]
    assert event['actor_id'] == 'reader' and event['payload']['path'] == 'docs/spec.md' and event['created_at']
    assert client.put(base + '/share', json={'paths':[]}).status_code == 200
    assert not (runtime / 'share/docs/spec.md').exists()
    assert (runtime / 'share/local.txt').read_text() == 'keep'
    assert (runtime / 'share/local-empty').is_dir()
    assert client.put(base + '/share', json={'paths':['docs']}).status_code == 200
    assert (runtime / 'share/docs').is_symlink()
    assert len(notices) == 3 and 'docs/spec.md' in notices[1][1]


def test_bounded_text_and_binary(tmp_path):
    target = tmp_path / 'large.txt'; target.write_text('あ' * 30000)
    assert read_path(target)['truncated'] and not read_path(target)['binary']
    target.write_bytes(b'\x00binary')
    assert read_path(target)['binary'] and not read_path(target)['content']


def test_upload_files_and_directories(tmp_path):
    project = tmp_path / 'project'
    root = project / 'share'
    root.mkdir(parents=True)
    (root / 'README.md').write_text('keep')
    (root / '.gitignore').write_text('keep')
    client = TestClient(create_app(project, tmp_path / 'data', tmp_path / 'auth'))
    room = client.get('/api/rooms').json()[0]
    assert client.post('/api/share/directories', params={'path': '資料'}).status_code == 200
    content = b'\x00binary\xff'
    assert client.put('/api/share/files', params={'path': '資料/nested/file.bin'}, content=content).status_code == 200
    assert (root / '資料/nested/file.bin').read_bytes() == content
    assert client.put('/api/share/files', params={'path': 'note.txt'}, content=b'note').status_code == 200
    assert (root / 'note.txt').read_bytes() == b'note'
    assert client.get(f"/api/rooms/{room['id']}").json()['share_contexts'] == []
    assert client.post('/api/share/directories', params={'path': '資料'}).status_code == 400
    for path in ['note.txt', 'README.md', '.gitignore', '資料/nested/file.bin', '資料']:
        assert client.put('/api/share/files', params={'path': path}, content=b'overwrite').status_code == 400
    assert (root / 'README.md').read_text() == 'keep'
    assert (root / '資料/nested/file.bin').read_bytes() == content


@pytest.mark.parametrize('path', ['', '../outside', '/absolute', 'docs/../outside', 'docs//file', 'docs/./file', 'docs\\file', '.git/config', 'node_modules/file', '.venv/file', '__pycache__/file', '.pytest_cache/file', 'docs/.git/config'])
def test_upload_rejects_invalid_paths(tmp_path, path):
    project = tmp_path / 'project'
    (project / 'share/docs').mkdir(parents=True)
    client = TestClient(create_app(project, tmp_path / 'data', tmp_path / 'auth'))
    assert client.put('/api/share/files', params={'path': path}, content=b'bad').status_code == 400
    assert client.post('/api/share/directories', params={'path': path}).status_code == 400


def test_upload_rejects_symlinks(tmp_path):
    project = tmp_path / 'project'
    root = project / 'share'
    (root / 'docs').mkdir(parents=True)
    (root / 'outside').symlink_to(tmp_path, target_is_directory=True)
    (root / 'alias').symlink_to(root / 'docs', target_is_directory=True)
    (root / 'dangling').symlink_to(root / 'missing')
    client = TestClient(create_app(project, tmp_path / 'data', tmp_path / 'auth'))
    for path in ['outside/file', 'alias/file', 'dangling']:
        assert client.put('/api/share/files', params={'path': path}, content=b'bad').status_code == 400
    assert not (root / 'docs/file').exists()
    assert not (root / 'missing').exists()
