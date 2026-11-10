#!/usr/bin/env python3
"""Scan maintained instructions for the withdrawn automatic-selection rules."""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FILES = [ROOT / "README.md", ROOT / "AGENTS.snippet.md", ROOT / "CHANGELOG.md",
         *list((ROOT / "single-conversation-subagent").rglob("*.md")),
         ROOT / "docs/单对话subagent-两级协作协议.md", ROOT / "docs/archify/skill-architecture.html"]
PATTERNS = [r"sleep\s+15", r"默认普通文本提问", r"默认用普通文本提问", r"仍未回复就继续",
            r"只有明确不选或客户端倒计时结束才按推荐项", r"默认/超时=不使用", r"超时也按它"]
violations = []
for path in FILES:
    text = path.read_text(encoding="utf-8")
    for pattern in PATTERNS:
        if re.search(pattern, text):
            violations.append(f"{path.relative_to(ROOT)}: withdrawn rule {pattern}")
if violations:
    print("\n".join(violations), file=sys.stderr)
    sys.exit(1)
print(f"PASS: {len(FILES)} maintained instruction/document files; 0 withdrawn-rule matches.")
