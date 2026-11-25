#!/usr/bin/env python3
"""Check ZCode agent profiles against the declared permission matrix.

Read-only: never edits profiles. Exits 1 on any missing, extra or mismatched
tool set. Usage:
    python scripts/check_agent_permissions.py [--agents-dir DIR] [--matrix FILE]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

TOOLS_LINE = re.compile(r"^tools:\s*\[(?P<tools>[^\]]*)\]\s*$", re.MULTILINE)
FRONTMATTER = re.compile(r"\A---\s*\n(?P<body>.*?)\n---\s*\n", re.DOTALL)
NAME_LINE = re.compile(r'^name:\s*"(?P<name>[^"]+)"\s*$', re.MULTILINE)


def parse_profile(path: Path) -> tuple[str, list[str]] | None:
    text = path.read_text(encoding="utf-8-sig")
    front = FRONTMATTER.match(text)
    if not front:
        return None
    body = front.group("body")
    name = NAME_LINE.search(body)
    tools = TOOLS_LINE.search(body)
    if not name or not tools:
        return None
    values = [item.strip() for item in tools.group("tools").split(",") if item.strip()]
    return name.group("name"), values


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser()
    parser.add_argument("--agents-dir", default=str(Path.home() / ".zcode" / "agents"))
    parser.add_argument("--matrix", default=str(root / "zcode-agents" / "permissions.json"))
    args = parser.parse_args()

    matrix = json.loads(Path(args.matrix).read_text(encoding="utf-8"))
    agents_dir = Path(args.agents_dir)
    if not agents_dir.is_dir():
        print(f"agents dir not found: {agents_dir}", file=sys.stderr)
        return 2

    failed = False
    seen: set[str] = set()
    for path in sorted(agents_dir.glob("*.md")):
        parsed = parse_profile(path)
        if parsed is None:
            print(f"SKIP  {path.name}: no frontmatter name/tools")
            continue
        name, tools = parsed
        if name not in matrix:
            continue
        seen.add(name)
        expected = matrix[name]
        if tools != expected:
            failed = True
            missing = [tool for tool in expected if tool not in tools]
            extra = [tool for tool in tools if tool not in expected]
            print(f"FAIL  {name} ({path.name})")
            print(f"      expected: {expected}")
            print(f"      actual:   {tools}")
            if missing:
                print(f"      missing:  {missing}")
            if extra:
                print(f"      extra:    {extra}")
        else:
            print(f"OK    {name}")
    for name in matrix:
        if name not in seen:
            failed = True
            print(f"FAIL  {name}: profile not found in {agents_dir}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
