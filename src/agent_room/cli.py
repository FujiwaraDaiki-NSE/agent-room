from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

import uvicorn

from .mcp_server import create_agent_room_mcp, request as mcp_request
from .server import create_app


def main() -> None:
    parser = argparse.ArgumentParser(prog="agent-room")
    subparsers = parser.add_subparsers(dest="command", required=True)

    serve = subparsers.add_parser("serve")
    serve.add_argument("--host", required=True)
    serve.add_argument("--port", required=True, type=int)
    serve.add_argument("--data-dir", required=True)
    serve.add_argument("--project-root", required=True)
    serve.add_argument("--codex-auth-file", required=True)

    mcp = subparsers.add_parser("mcp")
    add_server_args(mcp)
    mcp.add_argument("--room-id", required=True)
    mcp.add_argument("--agent-id", required=True)
    mcp.add_argument("--agent-name", required=True)
    mcp.add_argument("--controller", action="store_true")

    room = subparsers.add_parser("room")
    room_sub = room.add_subparsers(dest="room_command", required=True)

    read = room_sub.add_parser("read")
    add_server_args(read)
    read.add_argument("--room-id", required=True)
    read.add_argument("--after-id", type=int)

    post = room_sub.add_parser("post")
    add_server_args(post)
    post.add_argument("--room-id", required=True)
    post.add_argument("--agent-id", required=True)
    post.add_argument("--agent-name", required=True)
    post.add_argument("--text", required=True)

    done = room_sub.add_parser("done")
    add_server_args(done)
    done.add_argument("--room-id", required=True)
    done.add_argument("--agent-id", required=True)
    done.add_argument("--reason", required=True)

    controller = subparsers.add_parser("controller")
    controller_sub = controller.add_subparsers(dest="controller_command", required=True)

    controller_read = controller_sub.add_parser("read")
    add_server_args(controller_read)
    controller_read.add_argument("--room-id", required=True)
    controller_read.add_argument("--after-id", type=int)

    controller_post = controller_sub.add_parser("post")
    add_server_args(controller_post)
    controller_post.add_argument("--room-id", required=True)
    controller_post.add_argument("--agent-id", required=True)
    controller_post.add_argument("--agent-name", required=True)
    controller_post.add_argument("--text", required=True)

    agent = subparsers.add_parser("agent")
    agent_sub = agent.add_subparsers(dest="agent_command", required=True)

    deploy = agent_sub.add_parser("deploy")
    add_server_args(deploy)
    deploy.add_argument("--room-id", required=True)
    deploy.add_argument("--template-id", required=True)
    deploy.add_argument("--count", required=True, type=int)
    deploy.add_argument("--actor-id", required=True)

    stop = agent_sub.add_parser("stop")
    add_server_args(stop)
    stop.add_argument("--room-id", required=True)
    stop.add_argument("--agent-id", required=True)
    stop.add_argument("--actor-id", required=True)
    stop.add_argument("--reason", required=True)
    stop_mode = stop.add_mutually_exclusive_group(required=True)
    stop_mode.add_argument("--force", action="store_true")
    stop_mode.add_argument("--graceful", action="store_true")

    goal = agent_sub.add_parser("goal")
    add_server_args(goal)
    goal.add_argument("--room-id", required=True)
    goal.add_argument("--agent-id", required=True)
    goal.add_argument("--actor-id", required=True)
    goal.add_argument("--goal", required=True)
    goal.add_argument("--controller-termination", required=True)
    goal.add_argument("--agent-termination", required=True)

    config = agent_sub.add_parser("config")
    add_server_args(config)
    config.add_argument("--room-id", required=True)
    config.add_argument("--agent-id", required=True)
    config.add_argument("--actor-id", required=True)
    config.add_argument("--relative-path", required=True)
    config.add_argument("--content-file", required=True)
    config.add_argument("--reason", required=True)

    args = parser.parse_args()
    if args.command == "serve":
        server_url = f"http://{args.host}:{args.port}"
        os.environ["AGENT_ROOM_SERVER_URL"] = server_url
        app = create_app(
            Path(args.project_root).resolve(),
            Path(args.data_dir).resolve(),
            Path(args.codex_auth_file).resolve(),
        )
        uvicorn.run(app, host=args.host, port=args.port)
        return

    if args.command == "mcp":
        handle_mcp(args)
        return

    if args.command == "room":
        handle_room(args)
        return

    if args.command == "controller":
        handle_controller(args)
        return

    if args.command == "agent":
        handle_agent(args)
        return


def add_server_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--server", required=True)


def handle_mcp(args: argparse.Namespace) -> None:
    mcp = create_agent_room_mcp(
        server_url=args.server,
        room_id=args.room_id,
        agent_id=args.agent_id,
        agent_name=args.agent_name,
        is_controller=args.controller,
        request_fn=mcp_request,
    )
    mcp.run()


def handle_room(args: argparse.Namespace) -> None:
    if args.room_command == "read":
        query = ""
        if args.after_id is not None:
            query = "?" + urllib.parse.urlencode({"after_id": args.after_id})
        print_json(request("GET", f"{args.server}/api/rooms/{args.room_id}/messages{query}"))
        return
    if args.room_command == "post":
        payload = {
            "actor_type": "agent",
            "actor_id": args.agent_id,
            "actor_name": args.agent_name,
            "text": args.text,
            "kind": "message",
        }
        print_json(request("POST", f"{args.server}/api/rooms/{args.room_id}/messages", payload))
        return
    if args.room_command == "done":
        payload = {"actor_id": args.agent_id, "reason": args.reason}
        print_json(request("POST", f"{args.server}/api/rooms/{args.room_id}/agents/{args.agent_id}/done", payload))
        return
    raise SystemExit(f"unknown room command: {args.room_command}")


def handle_controller(args: argparse.Namespace) -> None:
    if args.controller_command == "read":
        query = ""
        if args.after_id is not None:
            query = "?" + urllib.parse.urlencode({"after_id": args.after_id})
        print_json(request("GET", f"{args.server}/api/rooms/{args.room_id}/controller/messages{query}"))
        return
    if args.controller_command == "post":
        payload = {
            "actor_type": "controller",
            "actor_id": args.agent_id,
            "actor_name": args.agent_name,
            "text": args.text,
            "kind": "message",
        }
        print_json(request("POST", f"{args.server}/api/rooms/{args.room_id}/controller/messages", payload))
        return
    raise SystemExit(f"unknown controller command: {args.controller_command}")


def handle_agent(args: argparse.Namespace) -> None:
    if args.agent_command == "deploy":
        payload = {"template_id": args.template_id, "count": args.count, "actor_id": args.actor_id}
        print_json(request("POST", f"{args.server}/api/rooms/{args.room_id}/agents", payload))
        return
    if args.agent_command == "stop":
        payload = {"actor_id": args.actor_id, "reason": args.reason, "force": args.force}
        print_json(request("POST", f"{args.server}/api/rooms/{args.room_id}/agents/{args.agent_id}/stop", payload))
        return
    if args.agent_command == "goal":
        payload = {
            "actor_id": args.actor_id,
            "goal": args.goal,
            "controller_termination": args.controller_termination,
            "agent_termination": args.agent_termination,
        }
        print_json(request("POST", f"{args.server}/api/rooms/{args.room_id}/agents/{args.agent_id}/goal", payload))
        return
    if args.agent_command == "config":
        content = Path(args.content_file).read_text(encoding="utf-8")
        payload = {
            "actor_id": args.actor_id,
            "relative_path": args.relative_path,
            "content": content,
            "reason": args.reason,
        }
        print_json(request("POST", f"{args.server}/api/rooms/{args.room_id}/agents/{args.agent_id}/config", payload))
        return
    raise SystemExit(f"unknown agent command: {args.agent_command}")


def request(method: str, url: str, payload: dict[str, Any] | None = None) -> Any:
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, method=method, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8")
        raise SystemExit(f"request failed: {exc.code} {body}") from exc


def print_json(value: Any) -> None:
    json.dump(value, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
