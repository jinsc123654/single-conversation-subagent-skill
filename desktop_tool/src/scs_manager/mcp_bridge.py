"""Run with the explicitly selected MCP Python (SDK 2.3); no Qt dependency."""
import argparse
import asyncio
import json
import sys
from pathlib import Path


async def run(args):
    close_request = None
    if args.action == "close":
        raw = sys.stdin.buffer.read(200_001)
        if len(raw) > 200_000:
            raise ValueError("关闭请求过大")
        close_request = json.loads(raw)
        if not isinstance(close_request, dict):
            raise ValueError("关闭请求格式错误")
        for key in ("package", "state", "reason"):
            if not isinstance(close_request.get(key), str) or not close_request[key].strip():
                raise ValueError("关闭请求缺少字段：" + key)
        if type(close_request.get("version")) is not int or close_request["version"] < 1:
            raise ValueError("关闭请求版本号无效")
    from mcp import Client
    from mcp.client.stdio import StdioServerParameters
    source = Path(__file__).resolve().parents[3] / "mcp_server/src"
    params = StdioServerParameters(command=sys.executable,
        args=["-m", "scs_mcp", "--project-root", args.root],
        env={"PYTHONPATH": str(source)} if source.is_dir() else None)
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
                "state": close_request["state"], "close_reason": close_request["reason"]})
        diagnostics = await call("diagnostics_check", {})
        tools = await client.list_tools()
        result = {"checked_at": __import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat(),
                  "diagnostics": diagnostics, "tools": [t.name for t in tools.tools],
                  "meaning": "本次短期 stdio 自检成功；实例随检查结束关闭，不代表宿主连接状态"}
        if args.session:
            result["view"] = await call("project_view_info", {"session_id": args.session})
        return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("probe", "refresh", "close"))
    parser.add_argument("--root", required=True)
    parser.add_argument("--session", default="")
    args = parser.parse_args()
    try:
        print(json.dumps({"ok": True, "data": asyncio.run(run(args))}, ensure_ascii=False))
    except Exception as error:
        # Do not copy SDK tracebacks or arbitrary client contents to desktop logs.
        message = str(error) if isinstance(error, ValueError) else "MCP 连接失败；核对解释器是否装有 scs-mcp 2.1 / mcp 2.3"
        print(json.dumps({"ok": False, "error": message}, ensure_ascii=False))
        sys.exit(1)
