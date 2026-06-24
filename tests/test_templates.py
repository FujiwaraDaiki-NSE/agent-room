import tomllib
from pathlib import Path

from agent_room.templates import TeamRegistry, TemplateRegistry


def test_templates_have_required_files() -> None:
    registry = TemplateRegistry(Path.cwd())
    templates = registry.list()

    ids = {template.id for template in templates}
    assert {
        "controller",
        "critic",
        "researcher",
        "builder",
        "synthesizer",
        "facilitator",
        "operator",
        "critique-technical",
        "critique-user",
        "critique-business",
        "critique-risk",
        "idea-reviser",
        "malcontent-user",
        "malcontent-ops",
        "malcontent-roaster",
        "hiroyuki",
    } <= ids

    for template in templates:
        assert registry.avatar_path(template.id).is_file()
        assert template.personality
        assert template.permissions

    personalities = [template.personality for template in templates]
    assert len(personalities) == len(set(personalities))


def test_templates_define_visible_speaking_tendency() -> None:
    registry = TemplateRegistry(Path.cwd())
    required_sections = [
        "## Role",
        "## Speaking Tendency",
        "## Judgment Criteria",
        "## Avoid",
        "## Self-check Before Posting",
    ]
    forbidden_text = [
        "## Personality",
        "## Voice",
        "Reference persona:",
        "Do not claim",
        "named character",
        "catchphrases",
    ]

    for template in registry.list():
        agents_md = registry.path_for(template.id) / "AGENTS.md"
        text = agents_md.read_text(encoding="utf-8")
        for section in required_sections:
            assert section in text
        for forbidden in forbidden_text:
            assert forbidden not in text


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


def test_templates_define_contextual_room_posting() -> None:
    registry = TemplateRegistry(Path.cwd())

    for template in registry.list():
        agents_md = registry.path_for(template.id) / "AGENTS.md"
        text = agents_md.read_text(encoding="utf-8")
        if template.scope == "controller":
            assert "Require public replies to start with `宛先: 全体`" in text
            assert "Do not accept low-context fragments" in text
        else:
            assert "Start each public post with `宛先: 全体`" in text
            assert "Write enough context for someone who has not followed the last few messages" in text
            assert "not label-only fragments" in text


def test_teams_reference_regular_agent_templates() -> None:
    registry = TemplateRegistry(Path.cwd())
    team_registry = TeamRegistry(Path.cwd(), registry)
    teams = team_registry.list()

    ids = {team.id for team in teams}
    assert teams[0].id == "default"
    assert "default" in ids
    assert "critique-lab" in ids
    assert "malcontent-table" in ids
    for team in teams:
        assert team.templates
        for template_id in team.templates:
            assert registry.get(template_id).scope == "agent"
