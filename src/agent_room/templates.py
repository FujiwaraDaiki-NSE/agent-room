from __future__ import annotations

import json
from pathlib import Path

from .models import AgentTeam, AgentTemplate


class TemplateError(RuntimeError):
    pass


class TemplateRegistry:
    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root
        self.controller_dir = project_root / "controller"
        self.agent_templates_dir = project_root / "agent-templates"

    def list(self) -> list[AgentTemplate]:
        templates: list[AgentTemplate] = []
        if self.controller_dir.exists():
            templates.append(self._load(self.controller_dir, "controller"))
        for path in sorted(self.agent_templates_dir.iterdir()):
            if path.is_dir():
                templates.append(self._load(path, "agent"))
        return templates

    def get(self, template_id: str) -> AgentTemplate:
        for template in self.list():
            if template.id == template_id:
                return template
        raise TemplateError(f"template not found: {template_id}")

    def path_for(self, template_id: str) -> Path:
        if template_id == "controller":
            path = self.controller_dir
        else:
            path = self.agent_templates_dir / template_id
        if not path.exists():
            raise TemplateError(f"template directory not found: {template_id}")
        return path

    def avatar_path(self, template_id: str) -> Path:
        template = self.get(template_id)
        avatar = self.path_for(template_id) / template.avatar
        if not avatar.is_file():
            raise TemplateError(f"avatar not found for template: {template_id}")
        return avatar

    def _load(self, path: Path, scope: str) -> AgentTemplate:
        manifest = path / "agent.json"
        if not manifest.is_file():
            raise TemplateError(f"agent.json missing: {path}")
        data = json.loads(manifest.read_text(encoding="utf-8"))
        data["scope"] = scope
        template = AgentTemplate.model_validate(data)
        avatar = path / template.avatar
        if not avatar.is_file():
            raise TemplateError(f"avatar missing: {avatar}")
        agents_md = path / "AGENTS.md"
        codex_config = path / ".codex" / "config.toml"
        if not agents_md.is_file():
            raise TemplateError(f"AGENTS.md missing: {path}")
        if not codex_config.is_file():
            raise TemplateError(f".codex/config.toml missing: {path}")
        return template


class TeamRegistry:
    def __init__(self, project_root: Path, template_registry: TemplateRegistry) -> None:
        self.project_root = project_root
        self.teams_dir = project_root / "agent-teams"
        self.template_registry = template_registry

    def list(self) -> list[AgentTeam]:
        teams: list[AgentTeam] = []
        for path in sorted(self.teams_dir.iterdir()):
            if path.is_dir():
                teams.append(self._load(path))
        return teams

    def get(self, team_id: str) -> AgentTeam:
        for team in self.list():
            if team.id == team_id:
                return team
        raise TemplateError(f"team not found: {team_id}")

    def _load(self, path: Path) -> AgentTeam:
        manifest = path / "team.json"
        if not manifest.is_file():
            raise TemplateError(f"team.json missing: {path}")
        team = AgentTeam.model_validate(json.loads(manifest.read_text(encoding="utf-8")))
        for template_id in team.templates:
            template = self.template_registry.get(template_id)
            if template.scope != "agent":
                raise TemplateError(f"team cannot include controller template: {team.id}")
        return team
