"""Run with the explicitly selected MCP Python (SDK 2.3); no Qt dependency."""
import argparse
import asyncio
import json
import os
import sys
from pathlib import Path


def build_server_env(native_review=False, environ=None):
    source_env = os.environ if environ is None else environ
    env = {"SCS_ALLOW_SESSION_LOOKUP": "1"}
    for name in ("SCS_BOARD_DIR", "SCS_NATIVE_REVIEW", "XDG_DATA_HOME", "HOME", "PATH", "LANG", "LC_ALL"):
        if name in source_env:
            env[name] = source_env[name]
    if native_review:
        env["SCS_NATIVE_REVIEW"] = "1"
    source = Path(__file__).resolve().parents[3] / "mcp_server/src"
    if source.is_dir():
        env["PYTHONPATH"] = str(source)
    return env


def parse_args(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("probe", "refresh", "close", "board-add", "board-start",
                                           "board-complete", "board-abandon", "board-prompt", "board-update"))
    parser.add_argument("--root", required=True)
    parser.add_argument("--session", default="")
    parser.add_argument("--native-review", action="store_true")
    return parser.parse_args(argv)


async def run(args):
    close_request = None
    payload = None
    board_actions = {"board-add", "board-start", "board-complete", "board-abandon",
                     "board-prompt", "board-update"}
    if args.action in board_actions or args.action == "close":
        raw = sys.stdin.buffer.read(200_001)
        if len(raw) > 200_000:
            raise ValueError("请求过大")
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError("请求格式错误")
    if args.action == "close":
        close_request = payload
        for key in ("package", "state", "reason"):
            if not isinstance(close_request.get(key), str) or not close_request[key].strip():
                raise ValueError("关闭请求缺少字段：" + key)
        if type(close_request.get("version")) is not int or close_request["version"] < 1:
            raise ValueError("关闭请求版本号无效")
        if "force" in close_request and type(close_request["force"]) is not bool:
            raise ValueError("关闭请求 force 必须是布尔值")
    if args.action == "board-add":
        if not isinstance(payload.get("title"), str) or not payload["title"].strip():
            raise ValueError("待办缺少标题")
    if args.action in {"board-start", "board-complete", "board-abandon", "board-prompt", "board-update"}:
        if not isinstance(payload.get("task_id"), str) or not payload["task_id"].strip():
            raise ValueError("待办请求缺少 task_id")
    if args.action == "board-abandon" and (not isinstance(payload.get("reason"), str) or not payload["reason"].strip()):
        raise ValueError("放弃待办必须填写原因")
    from mcp import Client
    from mcp.client.stdio import StdioServerParameters
    env = build_server_env(native_review=bool(getattr(args, "native_review", False)))
    params = StdioServerParameters(command=sys.executable,
        args=["-m", "scs_mcp", "--project-root", args.root], env=env)
    async with Client(params, read_timeout_seconds=8) as client:
        async def call(name, arguments):
            response = await client.call_tool(name, arguments)
            out = response.structured_content
            if not isinstance(out, dict) or response.is_error or out.get("status") == "ERROR":
                raise ValueError("MCP 操作失败：" + str((out or {}).get("error_code", "RPC_ERROR")))
            return out

        if args.action == "refresh":
            return await call("project_view_refresh", {"session_id": args.session, "initialize_vault": True})
        if args.action == "close":
            return await call("work_package_close", {"project_root": args.root,
                "package_id": close_request["package"], "expected_version": close_request["version"],
                "state": close_request["state"], "close_reason": close_request["reason"],
                "force": close_request.get("force", False)})
        if args.action == "board-add":
            return await call("board_task_add", {
                "title": payload["title"], "detail": payload.get("detail", ""),
                "project_root": args.root, "session_id": payload.get("session_id", ""),
                "priority": payload.get("priority", "medium"), "due_at": payload.get("due_at", ""),
                "actor": payload.get("actor", "gui")})
        if args.action == "board-start":
            return await call("board_task_start", {
                "task_id": payload["task_id"], "session_id": payload.get("session_id", ""),
                "owner": payload.get("owner", "gui")})
        if args.action == "board-complete":
            return await call("board_task_complete", {
                "task_id": payload["task_id"], "actor": payload.get("actor", "gui"),
                "summary": payload.get("summary", "")})
        if args.action == "board-abandon":
            return await call("board_task_abandon", {
                "task_id": payload["task_id"], "actor": payload.get("actor", "gui"),
                "reason": payload["reason"]})
        if args.action == "board-prompt":
            return await call("board_task_prompt", {"task_id": payload["task_id"]})
        if args.action == "board-update":
            return await call("board_task_update", {
                "task_id": payload["task_id"], "actor": payload.get("actor", "gui"),
                "title": payload.get("title"), "detail": payload.get("detail"),
                "priority": payload.get("priority"), "due_at": payload.get("due_at"),
                "session_id": payload.get("session_id")})
        diagnostics = await call("diagnostics_check", {})
        tools = await client.list_tools()
        result = {"checked_at": __import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat(),
                  "diagnostics": diagnostics, "tools": [t.name for t in tools.tools],
                  "meaning": "本次短期 stdio 自检成功；实例随检查结束关闭，不代表宿主连接状态"}
        if args.session:
            result["view"] = await call("project_view_info", {"session_id": args.session})
        return result


if __name__ == "__main__":
    args = parse_args()
    try:
        print(json.dumps({"ok": True, "data": asyncio.run(run(args))}, ensure_ascii=False))
    except Exception as error:
        # Do not copy SDK tracebacks or arbitrary client contents to desktop logs.
        message = str(error) if isinstance(error, ValueError) else "MCP 连接失败；核对解释器是否装有 scs-mcp 与 mcp 2.3"
        print(json.dumps({"ok": False, "error": message}, ensure_ascii=False))
        sys.exit(1)
