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


SHARE_COPY_IGNORE_PATTERNS = (
    "node_modules",
    ".venv",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    ".next",
    "dist",
    "build",
    "coverage",
    "tmp",
)


class TmuxManager:
    def __init__(self, project_root: Path, data_dir: Path, codex_auth_file: Path) -> None:
        self.project_root = project_root
        self.data_dir = data_dir
        self.codex_auth_file = codex_auth_file
        self.runtime_root = project_root / "runtime"
        self.share_root = project_root / "share"

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
        self._copy_share_contexts(runtime_dir, room.share_contexts)
        self._trust_project(runtime_dir)
        self._configure_mcp(runtime_dir, room, template, instance_id)
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

    def send_controller_whisper(self, agent: AgentInstance, text: str) -> None:
        if not agent.pane_id:
            raise TmuxError(f"controller has no tmux pane: {agent.id}")
        prompt = "\n".join(
            [
                "Private controller whisper from the user:",
                text,
                "",
                "This is not a public room message.",
                "Use controller_read if you need the private log, then reply with controller_post.",
                "Use room_post only if the response should be visible to every agent.",
            ]
        )
        try:
            subprocess.run(["tmux", "send-keys", "-t", agent.pane_id, prompt, "Enter"], check=True)
        except subprocess.CalledProcessError as exc:
            raise TmuxError(f"failed to notify controller pane: {agent.pane_id}") from exc

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
            *self._share_context_lines(room.share_contexts),
            "MCP tools:",
            "- room_read: read public room messages",
            "- room_post: post a public room message",
            "- room_done: mark yourself done",
            "- share_contexts: list selected shared context names",
            "- share_list: list files and directories in selected shared context",
            "- share_read: read a text file from selected shared context",
            "",
            "For shared context discovery, prefer share_contexts and share_list.",
            "Shared context directories are copied runtime snapshots, so shell tools can inspect ./share directly.",
            "",
            "Speak to the meeting only through the Agent Room MCP tools.",
            "Do not call the Agent Room HTTP API or CLI commands directly.",
        ]
        if template.scope == "controller":
            lines.extend(
                [
                    "",
                    "Controller MCP tools:",
                    "- controller_read: read private controller messages",
                    "- controller_post: post a private controller message",
                    "- agent_deploy: deploy agents",
                    "- agent_stop: stop an agent pane",
                    "- agent_goal: send a new goal to an agent",
                    "- agent_config: update an agent runtime config",
                    "- room_status_update: update the user-visible meeting status",
                    "- room_close_discussion: close public discussion for regular agents",
                    "- room_open_discussion: reopen public discussion for regular agents",
                    "- agent_mute: mute one regular agent's public messages",
                    "- agent_unmute: unmute one regular agent's public messages",
                    "",
                    "Use room_status_update before phase changes, after each round, and before final summaries.",
                    "Use controller tools for user-side whispers and lifecycle operations.",
                ]
            )
        return "\n".join(lines)

    def _share_context_lines(self, share_contexts: list[str]) -> list[str]:
        lines = [
            "Shared Context:",
            "Read selected shared context directories under ./share.",
        ]
        if share_contexts:
            lines.extend(f"- ./share/{name}" for name in share_contexts)
        else:
            lines.append("- No shared context selected.")
        lines.extend(["", "Treat shared context as read-only.", ""])
        return lines

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

    def _copy_share_contexts(self, runtime_dir: Path, share_contexts: list[str]) -> None:
        if not self.share_root.is_dir():
            raise TmuxError(f"share directory not found: {self.share_root}")
        runtime_share = runtime_dir / "share"
        if runtime_share.exists() or runtime_share.is_symlink():
            raise TmuxError(f"runtime share path already exists: {runtime_share}")
        runtime_share.mkdir()
        ignore = shutil.ignore_patterns(*SHARE_COPY_IGNORE_PATTERNS)
        for name in share_contexts:
            if not name or name in {".", ".."} or Path(name).name != name:
                raise TmuxError(f"share context must be a directory name: {name}")
            source = self.share_root / name
            if source.is_symlink() or not source.is_dir():
                raise TmuxError(f"share context not found: {name}")
            target = runtime_share / name
            shutil.copytree(source, target, ignore=ignore, symlinks=True)

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

    def _configure_mcp(
        self,
        runtime_dir: Path,
        room: Room,
        template: AgentTemplate,
        instance_id: str,
    ) -> None:
        server_url = os.environ.get("AGENT_ROOM_SERVER_URL")
        if not server_url:
            raise TmuxError("AGENT_ROOM_SERVER_URL is required")
        config_path = runtime_dir / ".codex" / "config.toml"
        if not config_path.is_file():
            raise TmuxError(f"runtime config file not found: {config_path}")
        args = [
            "run",
            "agent-room",
            "mcp",
            "--server",
            server_url,
            "--room-id",
            room.id,
            "--agent-id",
            instance_id,
            "--agent-name",
            template.name,
            "--share-root",
            str(runtime_dir / "share"),
        ]
        tools = ["room_read", "room_post", "room_done", "share_contexts", "share_list", "share_read"]
        if template.scope == "controller":
            args.append("--controller")
            tools.extend(
                [
                    "controller_read",
                    "controller_post",
                    "agent_deploy",
                    "agent_stop",
                    "agent_goal",
                    "agent_config",
                    "room_status_update",
                    "room_close_discussion",
                    "room_open_discussion",
                    "agent_mute",
                    "agent_unmute",
                ]
            )
        mcp_config = [
            '[mcp_servers.agent_room]',
            'command = "uv"',
            f"args = {json.dumps(args, ensure_ascii=False)}",
            f"cwd = {json.dumps(str(self.project_root))}",
            'default_tools_approval_mode = "approve"',
            "startup_timeout_sec = 20",
            "tool_timeout_sec = 60",
            f"enabled_tools = {json.dumps(tools)}",
        ]
        text = config_path.read_text(encoding="utf-8").rstrip()
        config_path.write_text(text + "\n\n" + "\n".join(mcp_config) + "\n", encoding="utf-8")

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
