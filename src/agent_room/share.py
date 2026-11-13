"""配布資料のパス検証と、上限付きの閲覧。"""
import codecs
from pathlib import Path

EXCLUDED = {'.git', 'node_modules', '.venv', '__pycache__', '.pytest_cache'}


def resolve_path(root: Path, path: str) -> Path:
    parts = path.split('/')
    if not path or Path(path).is_absolute() or any(p in {'', '.', '..'} or p in EXCLUDED for p in parts):
        raise ValueError(f'invalid share path: {path}')
    target = (root / path).resolve()
    if not target.is_relative_to(root.resolve()) or not target.exists():
        raise ValueError(f'share path outside root or missing: {path}')
    if any(part in EXCLUDED for part in target.relative_to(root.resolve()).parts):
        raise ValueError('excluded share directory')
    if not (target.is_file() or target.is_dir()):
        raise ValueError(f'unsupported share path: {path}')
    return target


def validate_paths(root: Path, paths: list[str]) -> list[str]:
    for index, path in enumerate(paths):
        resolve_path(root, path)
        for other in paths[:index]:
            if path == other or path.startswith(other + '/') or other.startswith(path + '/'):
                raise ValueError('share paths must not overlap')
    return paths


def selected_path(root: Path, paths: list[str], path: str) -> Path:
    target = resolve_path(root, path)
    for selected in paths:
        source = resolve_path(root, selected)
        if (path == selected or path.startswith(selected + '/')) and (target == source or source.is_dir() and target.is_relative_to(source)):
            return target
    raise ValueError('share path must stay inside the selected context')


def list_path(root: Path, path: str = '') -> list[dict]:
    target = resolve_path(root, path) if path else root
    if not target.is_dir():
        raise ValueError('share path is not a directory')
    items = []
    for child in sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name)):
        relative = f'{path}/{child.name}' if path else child.name
        try:
            resolved = resolve_path(root, relative)
        except ValueError:
            continue
        items.append({'name': child.name, 'relative_path': relative, 'type': 'dir' if resolved.is_dir() else 'file',
                      'size': resolved.stat().st_size if resolved.is_file() else None,
                      'count': sum(1 for p in resolved.iterdir() if p.name not in EXCLUDED) if resolved.is_dir() else None})
    return items


def read_path(target: Path) -> dict:
    if not target.is_file():
        raise ValueError('share path is not a file')
    with target.open('rb') as stream:
        data = stream.read(60001)
    binary = b'\0' in data
    try:
        content = codecs.getincrementaldecoder('utf-8')().decode(data[:60000], final=len(data) <= 60000)
    except UnicodeDecodeError:
        binary = True
        content = ''
    return {'content': '' if binary else content, 'binary': binary, 'truncated': len(data) > 60000, 'bytes': target.stat().st_size}
