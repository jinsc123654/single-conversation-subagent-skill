import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


def install(tmp_path, *args, env=None):
    return subprocess.run(["bash", str(REPO / "install.sh"), *args],
        env={**os.environ, "AGENT_HOME": str(tmp_path / "agent-home"), **(env or {})},
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
    assert version.stdout.strip() == b"2.4.2"
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
