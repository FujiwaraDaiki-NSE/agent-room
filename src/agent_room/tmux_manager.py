from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import time
import uuid
from pathlib import Path

from .models import AgentInstance, AgentTemplate, Room


class TmuxError(RuntimeError):
    pass


SESSION_ID_PATTERN = re.compile(
    r"rollout-\d{4}-\d{2}-\d{2}-([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\.jsonl?$"
)

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
        prompt = self._controller_whisper_prompt(text)
        try:
            subprocess.run(["tmux", "send-keys", "-t", agent.pane_id, prompt, "Enter"], check=True)
        except subprocess.CalledProcessError as exc:
            raise TmuxError(f"failed to notify controller pane: {agent.pane_id}") from exc

    def resume_controller(self, agent: AgentInstance, text: str) -> tuple[str, str]:
        if not os.environ.get("TMUX_PANE"):
            raise TmuxError("controller resume requires the server to run inside tmux")
        if agent.template_id != "controller":
            raise TmuxError(f"agent is not a controller: {agent.id}")
        if agent.pane_id:
            self.stop(agent, True)
        runtime_dir = self._agent_runtime_dir(agent)
        session_id = self.resolve_codex_session_id(agent)
        prompt_path = runtime_dir / "controller-resume-prompt.md"
        prompt_path.write_text(self._controller_whisper_prompt(text), encoding="utf-8")
        pane_id = self._split_pane(self._resume_command(runtime_dir, prompt_path, session_id, True))
        return pane_id, session_id

    def has_agent_exited(self, agent: AgentInstance) -> bool:
        if not agent.runtime_dir:
            return False
        return self._exit_marker(Path(agent.runtime_dir)).is_file()

    def resolve_codex_session_id(self, agent: AgentInstance) -> str:
        runtime_dir = self._agent_runtime_dir(agent)
        if agent.codex_session_id:
            if self._session_file_for_id(runtime_dir, agent.codex_session_id):
                return agent.codex_session_id
            raise TmuxError(f"codex session not found: {agent.codex_session_id}")
        session_ids = self._discover_codex_session_ids(runtime_dir)
        if len(session_ids) == 1:
            return session_ids[0]
        if not session_ids:
            raise TmuxError(f"codex session not found for agent: {agent.id}")
        raise TmuxError(f"codex session id is ambiguous for agent: {agent.id}")

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
                    "- room_finish: mark the room done and close all agent panes",
                    "- agent_mute: mute one regular agent's public messages",
                    "- agent_unmute: unmute one regular agent's public messages",
                    "",
                    "Use room_status_update before phase changes, after each round, and before final summaries.",
                    "Use room_close_discussion before the final public summary, then use room_finish after the outcome is complete.",
                    "Do not use room_done to finish the room; it only marks your controller agent done.",
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
        exit_marker = shlex.quote(str(self._exit_marker(runtime_dir)))
        permission_args = self._permission_args(can_write)
        return (
            f"cd {runtime} && "
            f"export CODEX_HOME={codex_home} && "
            f"rm -f {exit_marker} && "
            f'codex {permission_args} "$(cat {prompt_name})"; '
            f"touch {exit_marker}; "
            "exec bash"
        )

    def _resume_command(self, runtime_dir: Path, prompt_path: Path, session_id: str, can_write: bool) -> str:
        runtime = shlex.quote(str(runtime_dir))
        codex_home = shlex.quote(str(runtime_dir / ".codex"))
        prompt_name = shlex.quote(prompt_path.name)
        session = shlex.quote(session_id)
        exit_marker = shlex.quote(str(self._exit_marker(runtime_dir)))
        permission_args = self._permission_args(can_write)
        return (
            f"cd {runtime} && "
            f"export CODEX_HOME={codex_home} && "
            f"rm -f {exit_marker} && "
            f'codex resume {permission_args} {session} "$(cat {prompt_name})"; '
            f"touch {exit_marker}; "
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
                    "room_finish",
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

    def _agent_runtime_dir(self, agent: AgentInstance) -> Path:
        if not agent.runtime_dir:
            raise TmuxError(f"agent has no runtime directory: {agent.id}")
        runtime_dir = Path(agent.runtime_dir)
        if not runtime_dir.is_dir():
            raise TmuxError(f"agent runtime directory not found: {runtime_dir}")
        return runtime_dir

    def _controller_whisper_prompt(self, text: str) -> str:
        return "\n".join(
            [
                "Private controller whisper from the user:",
                text,
                "",
                "This is not a public room message.",
                "Use controller_read if you need the private log, then reply with controller_post.",
                "Use room_post only if the response should be visible to every agent.",
            ]
        )

    def _exit_marker(self, runtime_dir: Path) -> Path:
        return runtime_dir / ".codex" / "agent-exited"

    def _discover_codex_session_ids(self, runtime_dir: Path) -> list[str]:
        sessions_dir = runtime_dir / ".codex" / "sessions"
        if not sessions_dir.is_dir():
            return []
        session_ids = []
        for path in sorted(sessions_dir.rglob("rollout-*.json*"), key=lambda item: item.stat().st_mtime, reverse=True):
            match = SESSION_ID_PATTERN.match(path.name)
            if match and match.group(1) not in session_ids:
                session_ids.append(match.group(1))
        return session_ids

    def _session_file_for_id(self, runtime_dir: Path, session_id: str) -> Path | None:
        sessions_dir = runtime_dir / ".codex" / "sessions"
        if not sessions_dir.is_dir():
            return None
        for path in sessions_dir.rglob(f"rollout-*-{session_id}.json*"):
            if SESSION_ID_PATTERN.match(path.name):
                return path
        return None
