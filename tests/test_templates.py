from pathlib import Path

from agent_room.templates import TemplateRegistry


def test_templates_have_required_files() -> None:
    registry = TemplateRegistry(Path.cwd())
    templates = registry.list()

    ids = {template.id for template in templates}
    assert {"controller", "critic", "researcher", "builder", "synthesizer", "facilitator", "operator"} <= ids

    for template in templates:
        assert registry.avatar_path(template.id).is_file()
        assert template.personality
        assert template.permissions
