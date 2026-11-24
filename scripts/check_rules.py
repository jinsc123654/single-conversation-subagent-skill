#!/usr/bin/env python3
"""Scan maintained instructions for withdrawn rules, version drift, and ZCode agent consistency."""
import ast
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Explicitly enumerate the 6 managed Chinese ZCode agent templates so untracked
# legacy English drafts under zcode-agents/ are never scanned or treated as errors.
MANAGED_ZCODE_AGENTS = {
    "实施工程师": "实施工程师.md",
    "继承审阅员": "继承审阅员.md",
    "方案顾问": "方案顾问.md.in",
    "固定审阅员": "固定审阅员.md.in",
    "查询侦察员": "查询侦察员.md.in",
    "测试验证员": "测试验证员.md.in",
}

FILES = [
    ROOT / "README.md",
    ROOT / "AGENTS.snippet.md",
    ROOT / "CHANGELOG.md",
    *sorted((ROOT / "single-conversation-subagent").rglob("*.md")),
    *(ROOT / "zcode-agents" / filename for filename in MANAGED_ZCODE_AGENTS.values()),
    ROOT / "docs/单对话subagent-两级协作协议.md",
    ROOT / "docs/archify/skill-architecture.html",
]

PATTERNS = [
    r"sleep\s+15",
    r"默认普通文本提问",
    r"默认用普通文本提问",
    r"仍未回复就继续",
    r"只有明确不选或客户端倒计时结束才按推荐项",
    r"默认/超时=不使用",
    r"超时也按它",
]


def read_python_version(path: Path) -> str | None:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "__version__":
                    if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                        return node.value.value
    return None


def check_versions(violations: list[str]) -> str | None:
    version_file = ROOT / "VERSION"
    if not version_file.is_file():
        violations.append("VERSION: file missing")
        return None
    lines = version_file.read_text(encoding="utf-8").splitlines()
    expected = lines[0].strip() if lines else ""
    if not expected:
        violations.append("VERSION: line 1 is empty")
        return None

    pyproject_path = ROOT / "mcp_server/pyproject.toml"
    pyproject_ver = None
    if not pyproject_path.is_file():
        violations.append("mcp_server/pyproject.toml: file missing")
    else:
        data = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
        pyproject_ver = data.get("project", {}).get("version")

    mcp_init_path = ROOT / "mcp_server/src/scs_mcp/__init__.py"
    mcp_ver = None
    if not mcp_init_path.is_file():
        violations.append("mcp_server/src/scs_mcp/__init__.py: file missing")
    else:
        mcp_ver = read_python_version(mcp_init_path)

    desktop_init_path = ROOT / "desktop_tool/src/scs_manager/__init__.py"
    desktop_ver = None
    if not desktop_init_path.is_file():
        violations.append("desktop_tool/src/scs_manager/__init__.py: file missing")
    else:
        desktop_ver = read_python_version(desktop_init_path)

    for label, actual in [
        ("mcp_server/pyproject.toml [project].version", pyproject_ver),
        ("mcp_server/src/scs_mcp/__init__.py __version__", mcp_ver),
        ("desktop_tool/src/scs_manager/__init__.py __version__", desktop_ver),
    ]:
        if actual != expected:
            violations.append(f"version mismatch: {label}={actual!r} != VERSION={expected!r}")

    return expected


def check_managed_zcode_agents(violations: list[str]) -> None:
    for role_name, filename in MANAGED_ZCODE_AGENTS.items():
        path = ROOT / "zcode-agents" / filename
        if not path.is_file():
            violations.append(f"zcode-agents/{filename}: managed ZCode profile missing")
            continue
        text = path.read_text(encoding="utf-8")
        frontmatter = re.match(r"\A---\n(.*?)\n---(?:\n|\Z)", text, re.S)
        if not frontmatter:
            violations.append(f"zcode-agents/{filename}: missing YAML frontmatter")
            continue
        name_match = re.search(r'^name:\s*"([^"]+)"\s*$', frontmatter.group(1), re.M)
        if not name_match or name_match.group(1) != role_name:
            found = name_match.group(1) if name_match else None
            violations.append(f"zcode-agents/{filename}: frontmatter name={found!r} != {role_name!r}")

    install_path = ROOT / "install.sh"
    if not install_path.is_file():
        violations.append("install.sh: file missing")
        return
    install_text = install_path.read_text(encoding="utf-8")

    registered: list[str] = []
    for match in re.finditer(r"ZCODE_PROFILE_NAMES\+=\(([^)]*)\)|ZCODE_PROFILE_NAMES=\(([^)]*)\)", install_text):
        raw = match.group(1) if match.group(1) is not None else match.group(2)
        registered.extend(raw.split())
    if len(registered) != len(set(registered)):
        violations.append(f"install.sh: duplicate names in ZCODE_PROFILE_NAMES: {registered}")
    expected_names = set(MANAGED_ZCODE_AGENTS)
    registered_set = set(registered)
    if registered_set != expected_names:
        missing = sorted(expected_names - registered_set)
        extra = sorted(registered_set - expected_names)
        violations.append(
            f"install.sh: ZCODE_PROFILE_NAMES mismatch (missing={missing}, extra={extra})"
        )

    tmpl_match = re.search(
        r'zcode_profile_template\(\)\s*\{.*?case\s+"\$1"\s+in\s*\n\s*([^\n)]+)\)\s+printf\s+\'%s\\n\'\s+"\$SCRIPT_DIR/zcode-agents/\$1\.md\.in"',
        install_text,
        re.S,
    )
    expected_templates = {k for k, v in MANAGED_ZCODE_AGENTS.items() if v.endswith(".md.in")}
    if not tmpl_match:
        violations.append("install.sh: could not verify .md.in mapping in zcode_profile_template()")
    else:
        actual_templates = {item.strip() for item in tmpl_match.group(1).split("|") if item.strip()}
        if actual_templates != expected_templates:
            violations.append(
                f"install.sh: zcode_profile_template() .md.in set {sorted(actual_templates)} != {sorted(expected_templates)}"
            )


def main() -> int:
    violations: list[str] = []
    scanned = 0
    for path in FILES:
        if not path.is_file():
            violations.append(f"{path.relative_to(ROOT)}: maintained file missing")
            continue
        scanned += 1
        text = path.read_text(encoding="utf-8")
        for pattern in PATTERNS:
            if re.search(pattern, text):
                violations.append(f"{path.relative_to(ROOT)}: withdrawn rule {pattern}")

    version = check_versions(violations)
    check_managed_zcode_agents(violations)

    if violations:
        print("\n".join(violations), file=sys.stderr)
        return 1
    print(
        f"PASS: {scanned} maintained files scanned (0 withdrawn-rule matches); "
        f"versions aligned ({version}); {len(MANAGED_ZCODE_AGENTS)} managed ZCode agents verified against install.sh."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
