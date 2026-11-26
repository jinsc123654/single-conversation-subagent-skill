import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from scs_mcp import __version__

REPO = Path(__file__).resolve().parents[2]
ZCODE_PROFILES = {
    "方案顾问": "scs-advisor",
    "固定审阅员": "scs-fixed-reviewer",
    "继承审阅员": "scs-inherited-reviewer",
    "查询侦察员": "scs-scout",
    "实施工程师": "scs-implementer",
    "测试验证员": "scs-test-verifier",
    "设备与端到端验证员": "scs-device-verifier",
}


def install(tmp_path, *args, env=None):
    return subprocess.run(["bash", str(REPO / "install.sh"), *args],
        env={**os.environ, "HOME": str(tmp_path / "home"),
             "AGENT_HOME": str(tmp_path / "agent-home"), **(env or {})},
        text=True, capture_output=True, timeout=20)


def test_install_repeat_uninstall_preserves_other_markers_and_backups(tmp_path):
    home = tmp_path / "agent-home"
    home.mkdir()
    rules = home / "AGENTS.md"
    original = "USER HEADER\n<!-- other-project begin -->\nkeep me\n<!-- other-project end -->\n"
    rules.write_text(original)
    assert install(tmp_path, "--with-default-rule").returncode == 0
    once = rules.read_text()
    assert once.startswith(original)
    assert once.count("<!-- single-conversation-subagent begin -->") == 1
    assert install(tmp_path, "--with-default-rule").returncode == 0
    assert rules.read_text() == once
    assert install(tmp_path, "--check").returncode == 0
    assert list(home.glob("AGENTS.md.bak.*"))
    (home / "user-client-config.json").write_text('{"unrelated": true}')
    assert install(tmp_path, "--uninstall").returncode == 0
    assert "keep me" in rules.read_text() and "single-conversation-subagent begin" not in rules.read_text()
    assert (home / "user-client-config.json").read_text() == '{"unrelated": true}'
    assert not (home / "skills/single-conversation-subagent").exists()
    assert install(tmp_path, "--check").returncode != 0


@pytest.mark.parametrize("malformed", ["<!-- single-conversation-subagent end -->\n", "<!-- single-conversation-subagent begin -->\n",
    "<!-- single-conversation-subagent end -->\n<!-- single-conversation-subagent begin -->\n"])
def test_malformed_rule_preflight_does_not_modify_anything(tmp_path, malformed):
    home = tmp_path / "agent-home"
    home.mkdir()
    (home / "AGENTS.md").write_text(malformed)
    result = install(tmp_path, "--with-default-rule")
    assert result.returncode != 0
    assert (home / "AGENTS.md").read_text() == malformed
    assert not (home / "skills").exists()


def test_symlinked_parent_preflight(tmp_path):
    target = tmp_path / "other"
    target.mkdir()
    (tmp_path / "agent-home").symlink_to(target)
    assert install(tmp_path).returncode != 0
    assert list(target.iterdir()) == []


def test_custom_instructions_and_missing_python_keep_skill(tmp_path):
    rules = tmp_path / "custom.md"
    rules.write_text("Keep this\n")
    result = install(tmp_path, "--mode", "hybrid", "--python", "/missing/python", "--instructions-file", str(rules))
    assert result.returncode == 0 and "HYBRID_UNAVAILABLE" in result.stderr
    assert (tmp_path / "agent-home/skills/single-conversation-subagent/SKILL.md").exists()
    assert rules.read_text().startswith("Keep this\n")
    assert install(tmp_path, "--uninstall", "--instructions-file", str(rules)).returncode == 0
    assert "Keep this" in rules.read_text() and "single-conversation-subagent begin" not in rules.read_text()


def test_check_never_creates_installation(tmp_path):
    assert install(tmp_path, "--mode", "hybrid", "--check").returncode != 0
    assert not (tmp_path / "agent-home").exists()


def test_hybrid_real_runtime_and_examples(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    args = ("--mode", "hybrid", "--python", sys.executable, "--project-root", str(root))
    result = install(tmp_path, *args)
    assert result.returncode == 0, result.stderr
    runtime = tmp_path / "agent-home/mcp/single-conversation-subagent"
    examples = json.loads((runtime / "client-examples.json").read_text())
    entry = examples["mcpServers"]["single-conversation-subagent"]
    assert entry["command"] == sys.executable
    assert entry["args"][-1] == str(root)
    version = subprocess.run([sys.executable, "launcher.py", "--version"], cwd=str(runtime),
                             capture_output=True, timeout=5, shell=False)
    assert version.stdout.strip() == __version__.encode()
    assert install(tmp_path, *args).returncode == 0
    assert install(tmp_path, "--mode", "hybrid", "--check").returncode == 0
    assert not (root / ".scs").exists()  # --version/check never creates project state


def test_runtime_child_symlink_rejected_before_install(tmp_path):
    home = tmp_path / "agent-home"
    runtime = home / "mcp/single-conversation-subagent"
    runtime.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (runtime / "src").symlink_to(outside)
    assert install(tmp_path, "--mode", "hybrid", "--python", sys.executable).returncode != 0
    assert list(outside.iterdir()) == []
    assert not (home / "skills").exists()


def test_zcode_installs_missing_agents_and_preserves_existing_models(tmp_path):
    home = tmp_path / "agent-home"
    agents = tmp_path / "zcode-agents"
    args = ("--host", "zcode", "--with-zcode-agents", "--zcode-agents-dir", str(agents),
            "--zcode-gpt-model", "local/gpt", "--zcode-deepseek-model", "local/deepseek",
            "--zcode-deepseek-thought-level", "max")
    result = install(tmp_path, *args, "--mode", "hybrid", "--python", sys.executable,
                     "--project-root", str(tmp_path))
    assert result.returncode == 0, result.stderr
    assert {p.stem for p in agents.glob("*.md")} == set(ZCODE_PROFILES.values())
    matrix = json.loads((REPO / "zcode-agents/permissions.json").read_text(encoding="utf-8"))
    assert matrix.keys() == set(ZCODE_PROFILES.values())
    for label, name in ZCODE_PROFILES.items():
        profile = (agents / f"{name}.md").read_text(encoding="utf-8")
        _, frontmatter, _ = profile.split("---\n", 2)
        fields = dict(line.split(": ", 1) for line in frontmatter.splitlines())
        assert json.loads(fields["name"]) == name
        assert re.fullmatch(r"[A-Za-z0-9-]{3,50}", name)
        assert label in fields["description"]
        assert fields["tools"] == "[" + ", ".join(matrix[name]) + "]"
        if name in {"scs-scout", "scs-test-verifier", "scs-device-verifier"}:
            assert fields["model"] == '"local/deepseek"'
            assert fields["thoughtLevel"] == '"max"'
        elif name in {"scs-advisor", "scs-fixed-reviewer"}:
            assert fields["model"] == '"local/gpt"'
            assert "thoughtLevel" not in fields
        else:
            assert fields["model"] == '"inherit"'
    advisor = (agents / "scs-advisor.md").read_text(encoding="utf-8")
    assert "长任务在确认验证与复核策略后派发一次" in advisor
    example = json.loads((home / "mcp/single-conversation-subagent/zcode-example.json").read_text())
    assert example["mcp"]["servers"]["single-conversation-subagent"]["env"]["SCS_NATIVE_REVIEW"] == "1"

    existing = agents / "scs-fixed-reviewer.md"
    custom = b'---\nname: "scs-fixed-reviewer"\nmodel: "local/existing"\n---\n\nCustom prompt.\n'
    existing.write_bytes(custom)
    existing.chmod(0o600)
    result = install(tmp_path, *args)
    assert result.returncode == 0, result.stderr
    assert "Preserved existing ZCode profile path" in result.stdout
    assert existing.read_bytes() == custom
    assert existing.stat().st_mode & 0o777 == 0o600


def test_zcode_skips_fixed_profiles_without_model_options(tmp_path):
    agents = tmp_path / "home/.zcode/agents"
    result = install(tmp_path, "--host", "zcode", "--with-zcode-agents")
    assert result.returncode == 0, result.stderr
    assert {p.stem for p in agents.glob("*.md")} == {"scs-implementer", "scs-inherited-reviewer"}
    assert "scs-device-verifier" in result.stdout


@pytest.mark.parametrize("legacy_name,new_id", ZCODE_PROFILES.items())
def test_zcode_legacy_profile_stops_before_installing_anything(tmp_path, legacy_name, new_id):
    agents = tmp_path / "home/.zcode/agents"
    agents.mkdir(parents=True)
    legacy = agents / f"{legacy_name}.md"
    content = f'---\nname: "{legacy_name}"\nmodel: "local/custom"\n---\n'.encode("utf-8")
    legacy.write_bytes(content)
    rules = tmp_path / "AGENTS.md"
    rules.write_text("Unrelated instructions\n", encoding="utf-8")

    result = install(tmp_path, "--host", "zcode", "--with-zcode-agents",
                     "--instructions-file", str(rules))
    assert result.returncode != 0
    assert f'{legacy} -> {new_id}.md (name: "{new_id}")' in result.stderr
    assert "stopped before making changes" in result.stderr
    assert "profiles/README.md" in result.stderr
    assert not (tmp_path / "agent-home").exists()
    assert rules.read_text(encoding="utf-8") == "Unrelated instructions\n"
    assert list(agents.iterdir()) == [legacy]
    assert legacy.read_bytes() == content


def test_zcode_legacy_dangling_symlink_and_new_profile_are_not_touched(tmp_path):
    agents = tmp_path / "profiles"
    agents.mkdir()
    legacy = agents / "固定审阅员.md"
    missing = tmp_path / "missing.md"
    legacy.symlink_to(missing)
    existing = agents / "scs-fixed-reviewer.md"
    existing.write_bytes(b"Configured replacement\n")

    result = install(tmp_path, "--host", "zcode", "--with-zcode-agents",
                     "--zcode-agents-dir", str(agents), "--zcode-gpt-model", "local/gpt")
    assert result.returncode != 0
    assert "without overwriting either file" in result.stderr
    assert not (tmp_path / "agent-home").exists()
    assert set(agents.iterdir()) == {legacy, existing}
    assert legacy.readlink() == missing and not missing.exists()
    assert existing.read_bytes() == b"Configured replacement\n"
