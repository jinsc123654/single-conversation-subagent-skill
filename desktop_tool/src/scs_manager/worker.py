"""One bounded task per process. The GUI can reap its own process group."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sqlite3
import sys

from .backend import archive_view, database, discover, snapshot, project_catalog, prepare_obsidian


def main():
    if os.name == "posix":
        os.setsid()
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("snapshot", "scan", "probe", "refresh", "archive", "catalog", "prepare-obsidian"))
    parser.add_argument("--root", required=True)
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--session", default="")
    parser.add_argument("--selection", default="")
    args = parser.parse_args()
    try:
        if args.action == "catalog":
            raw=sys.stdin.buffer.read(2_000_001)
            if len(raw)>2_000_000:
                raise ValueError("项目列表过大")
            data=project_catalog(json.loads(raw))
        elif args.action == "prepare-obsidian":
            data=prepare_obsidian(args.root,args.selection or None)
        elif args.action == "snapshot":
            data = snapshot(args.root)
        elif args.action == "scan":
            data = discover(args.root)
        elif args.action == "archive":
            data = archive_view(args.root)
        else:
            # Connecting starts the configured server: only accept already upgraded
            # schema 2 here, so a status button cannot silently migrate an old DB.
            with database(args.root) as con:
                if con.execute("PRAGMA user_version").fetchone()[0] != 2:
                    raise ValueError("请先按 MCP 升级说明备份并升级旧状态库；此按钮不会替你迁移")
            bridge = Path(__file__).with_name("mcp_bridge.py")
            result = subprocess.run([args.python, str(bridge), args.action, "--root", args.root,
                                     "--session", args.session], capture_output=True, timeout=22)
            if len(result.stdout) > 2_000_000:
                raise ValueError("响应超过限制")
            reply = json.loads(result.stdout)
            if not reply.get("ok"):
                raise ValueError(reply.get("error", "MCP 操作失败"))
            data = reply["data"]
        print(json.dumps({"ok": True, "data": data}, ensure_ascii=False))
    except Exception as error:
        message = str(error) if isinstance(error, (ValueError, OSError, sqlite3.Error)) else "操作失败，请检查项目状态或解释器"
        print(json.dumps({"ok": False, "error": message}, ensure_ascii=False))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
