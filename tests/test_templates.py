import tomllib
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

    personalities = [template.personality for template in templates]
    assert len(personalities) == len(set(personalities))


def test_templates_define_visible_personality() -> None:
    registry = TemplateRegistry(Path.cwd())

    for template in registry.list():
        agents_md = registry.path_for(template.id) / "AGENTS.md"
        text = agents_md.read_text(encoding="utf-8")
        assert "## Personality" in text


def test_regular_agent_templates_use_mini_model() -> None:
    registry = TemplateRegistry(Path.cwd())

    for template in registry.list():
        config_path = registry.path_for(template.id) / ".codex" / "config.toml"
        config = tomllib.loads(config_path.read_text(encoding="utf-8"))
        if template.scope == "controller":
            assert "model" not in config
        else:
            assert config["model"] == "gpt-5.4-mini"


def test_templates_define_meeting_authority() -> None:
    registry = TemplateRegistry(Path.cwd())

    for template in registry.list():
        agents_md = registry.path_for(template.id) / "AGENTS.md"
        text = agents_md.read_text(encoding="utf-8")
        if template.scope == "controller":
            assert "## User Authority" in text
            assert "User instructions override" in text
            assert "Control the meeting actively" in text
        else:
            assert "## Controller Authority" in text
            assert "Follow controller instructions" in text
            assert "mark yourself done" in text
            assert "override the normal round protocol" in text
