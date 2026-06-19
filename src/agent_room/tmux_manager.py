from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import time
import uuid
from pathlib import Path

from .models import AgentInstance, AgentTemplate, Room


class TmuxError(RuntimeError):
    pass


class TmuxManager:
    def __init__(self, project_root: Path, data_dir: Path, codex_auth_file: Path) -> None:
        self.project_root = project_root
        self.data_dir = data_dir
        self.codex_auth_file = codex_auth_file
        self.runtime_root = project_root / "runtime"

    def status(self) -> dict[str, str | bool]:
        pane = os.environ.get("TMUX_PANE")
        session = self._tmux_value("#S") if pane else ""
        window = self._tmux_value("#W") if pane else ""
        attach = f"tmux attach -t {session}" if session else ""
        return {
            "inside_tmux": bool(pane),
            "pane": pane or "",
            "session": session,
            "window": window,
            "attach_command": attach,
        }

    def deploy(
        self,
        room: Room,
        template: AgentTemplate,
        template_dir: Path,
        actor_id: str,
        goal: str,
        controller_termination: str,
        agent_termination: str,
    ) -> AgentInstance:
        if not os.environ.get("TMUX_PANE"):
            raise TmuxError("agent deploy requires the server to run inside tmux")
        instance_id = f"{template.id}-{uuid.uuid4().hex[:6]}"
        runtime_dir = self.runtime_root / "rooms" / room.id / "agents" / instance_id
        if runtime_dir.exists():
            raise TmuxError(f"runtime directory already exists: {runtime_dir}")
        shutil.copytree(template_dir, runtime_dir)
        self._trust_project(runtime_dir)
        self._link_shared_auth(runtime_dir)
        prompt_path = runtime_dir / "room-goal.md"
        prompt_path.write_text(
            self._goal_prompt(room, template, instance_id, goal, controller_termination, agent_termination),
            encoding="utf-8",
        )
        command = self._agent_command(runtime_dir, prompt_path, template.scope == "controller")
        pane_id = self._split_pane(command)
        avatar_url = f"/api/templates/{template.id}/avatar"
        return AgentInstance(
            id=instance_id,
            room_id=room.id,
            template_id=template.id,
            name=template.name,
            short_name=template.short_name,
            role=template.role,
            personality=template.personality,
            accent=template.accent,
            avatar_url=avatar_url,
            state="active",
            pane_id=pane_id,
            runtime_dir=str(runtime_dir),
            goal=goal,
            controller_termination=controller_termination if template.scope == "controller" else None,
            agent_termination=agent_termination,
        )

    def stop(self, agent: AgentInstance, force: bool) -> None:
        if not agent.pane_id:
            raise TmuxError(f"agent has no tmux pane: {agent.id}")
        if not force:
            subprocess.run(["tmux", "send-keys", "-t", agent.pane_id, "/exit", "Enter"], check=False)
            time.sleep(1.0)
        subprocess.run(["tmux", "kill-pane", "-t", agent.pane_id], check=False)

    def send_goal(
        self,
        agent: AgentInstance,
        goal: str,
        controller_termination: str,
        agent_termination: str,
    ) -> None:
        if not agent.pane_id:
            raise TmuxError(f"agent has no tmux pane: {agent.id}")
        text = "\n".join(
            self._goal_lines(agent.template_id == "controller", goal, controller_termination, agent_termination)
        )
        subprocess.run(["tmux", "send-keys", "-t", agent.pane_id, text, "Enter"], check=True)

    def _goal_prompt(
        self,
        room: Room,
        template: AgentTemplate,
        instance_id: str,
        goal: str,
        controller_termination: str,
        agent_termination: str,
    ) -> str:
        server_url = os.environ.get("AGENT_ROOM_SERVER_URL")
        if not server_url:
            raise TmuxError("AGENT_ROOM_SERVER_URL is required")
        goal_lines = self._goal_lines(
            template.scope == "controller",
            goal,
            controller_termination,
            agent_termination,
        )
        lines = [
            "/goal",
            f"Room: {room.id}",
            f"Agent: {instance_id}",
            f"Role: {template.name}",
            "",
            *goal_lines[1:],
            "Room commands:",
            f"- Read: uv run agent-room room read --server {server_url} --room-id {room.id}",
            f"- Post: uv run agent-room room post --server {server_url} --room-id {room.id} --agent-id {instance_id} --agent-name {shlex.quote(template.name)} --text '<message>'",
            f"- Done: uv run agent-room room done --server {server_url} --room-id {room.id} --agent-id {instance_id} --reason '<reason>'",
            "",
            "Speak to the meeting only through the room commands.",
        ]
        if template.scope == "controller":
            lines.extend(
                [
                    "",
                    "Controller private commands:",
                    f"- Read private: uv run agent-room controller read --server {server_url} --room-id {room.id}",
                    f"- Reply private: uv run agent-room controller post --server {server_url} --room-id {room.id} --agent-id {instance_id} --agent-name {shlex.quote(template.name)} --text '<message>'",
                    "",
                    "Use the private commands for user-side whispers that should not go to the public room log.",
                ]
            )
        return "\n".join(lines)

    def _goal_lines(
        self,
        is_controller: bool,
        goal: str,
        controller_termination: str,
        agent_termination: str,
    ) -> list[str]:
        lines = [
            "/goal",
            "Goal:",
            goal,
            "",
        ]
        if is_controller:
            lines.extend(
                [
                    "Controller Termination:",
                    controller_termination,
                    "",
                    "Agent Termination:",
                    agent_termination,
                    "",
                ]
            )
        else:
            lines.extend(
                [
                    "Agent Termination:",
                    agent_termination,
                    "",
                ]
            )
        return lines

    def _agent_command(self, runtime_dir: Path, prompt_path: Path, can_write: bool) -> str:
        runtime = shlex.quote(str(runtime_dir))
        codex_home = shlex.quote(str(runtime_dir / ".codex"))
        prompt_name = shlex.quote(prompt_path.name)
        permission_args = self._permission_args(can_write)
        return (
            f"cd {runtime} && "
            f"export CODEX_HOME={codex_home} && "
            f'codex {permission_args} "$(cat {prompt_name})"; '
            "exec bash"
        )

    def _permission_args(self, can_write: bool) -> str:
        if can_write:
            args = [
                "--sandbox",
                "workspace-write",
                "--ask-for-approval",
                "never",
                "-c",
                "sandbox_workspace_write.network_access=true",
            ]
        else:
            args = [
                "--ask-for-approval",
                "never",
                "-c",
                'default_permissions="agent_read_network"',
                "-c",
                'permissions.agent_read_network.extends=":read-only"',
                "-c",
                "permissions.agent_read_network.network.enabled=true",
            ]
        return " ".join(shlex.quote(arg) for arg in args)

    def _link_shared_auth(self, runtime_dir: Path) -> None:
        if not self.codex_auth_file.is_file():
            raise TmuxError(f"codex auth file not found: {self.codex_auth_file}")
        target = runtime_dir / ".codex" / "auth.json"
        if target.exists() or target.is_symlink():
            raise TmuxError(f"runtime auth file already exists: {target}")
        target.symlink_to(self.codex_auth_file)

    def _trust_project(self, runtime_dir: Path) -> None:
        config_path = runtime_dir / ".codex" / "config.toml"
        if not config_path.is_file():
            raise TmuxError(f"runtime config file not found: {config_path}")
        text = config_path.read_text(encoding="utf-8")
        project_table = f"[projects.{json.dumps(str(self.project_root))}]"
        if project_table in text:
            return
        config_path.write_text(
            text.rstrip() + f"\n\n{project_table}\ntrust_level = \"trusted\"\n",
            encoding="utf-8",
        )

    def _split_pane(self, command: str) -> str:
        target = os.environ["TMUX_PANE"]
        result = subprocess.run(
            ["tmux", "split-window", "-t", target, "-h", "-P", "-F", "#{pane_id}", command],
            check=True,
            text=True,
            capture_output=True,
        )
        subprocess.run(["tmux", "select-layout", "tiled"], check=False)
        return result.stdout.strip()

    def _tmux_value(self, pattern: str) -> str:
        result = subprocess.run(
            ["tmux", "display-message", "-p", pattern],
            check=True,
            text=True,
            capture_output=True,
        )
        return result.stdout.strip()
