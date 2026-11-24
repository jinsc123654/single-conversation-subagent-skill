import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from scs_mcp import __version__

REPO = Path(__file__).resolve().parents[2]


def install(tmp_path, *args, env=None):
    return subprocess.run(["bash", str(REPO / "install.sh"), *args],
        cwd=tmp_path,
        env={**os.environ, "HOME": str(tmp_path / "fake-home"),
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
    zcode = json.loads((runtime / "zcode-example.json").read_text())
    assert zcode == {"mcp": {"servers": {"single-conversation-subagent": {
        "type": "stdio", "command": sys.executable, "args": entry["args"], "timeoutMs": 60000,
        "env": {"SCS_HOST": "zcode", "SCS_QUESTION_CHANNEL": "native", "SCS_NATIVE_REVIEW": "1"},
    }}}}
    version = subprocess.run([sys.executable, "launcher.py", "--version"], cwd=str(runtime),
                             capture_output=True, timeout=5, shell=False)
    assert version.stdout.strip() == __version__.encode()
    assert install(tmp_path, *args).returncode == 0
    assert install(tmp_path, "--mode", "hybrid", "--check").returncode == 0
    assert not (root / ".scs").exists()  # --version/check never creates project state


@pytest.mark.parametrize("published", [False, True], ids=["before-publish", "after-publish"])
def test_bootstrap_cleanup_on_term_at_python_path_publish(tmp_path, published):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    bootstrap = bin_dir / "bootstrap-python"
    bootstrap.write_text('''#!/bin/bash
if [ "$1" = -m ] && [ "$2" = venv ]; then
  mkdir -p "$3/bin"
  printf '%s\\n' '#!/bin/bash' \
    'if [[ "$1" = -m && "$2" = pip ]]; then exit 0; fi' \
    'if [[ "$1" = -c && "$2" == *"print(sys.executable)"* ]]; then printf "%s\\n" "$0"; exit 0; fi' \
    'exec "$REAL_PYTHON" "$@"' > "$3/bin/python"
  chmod +x "$3/bin/python"
  exit
fi
if [[ "$1" = -c && "$2" == *importlib.metadata* ]]; then exit 1; fi
exec "$REAL_PYTHON" "$@"
''')
    bootstrap.chmod(0o755)
    mv = bin_dir / "mv"
    mv.write_text('''#!/bin/bash
if [[ "${@: -1}" == */python-path ]]; then
  if [ "$PUBLISHED" = 1 ]; then "$REAL_MV" "$@" || exit; fi
  kill -TERM "$PPID"
  exit
fi
exec "$REAL_MV" "$@"
''')
    mv.chmod(0o755)
    result = install(tmp_path, "--mode", "hybrid", "--python", str(bootstrap), env={
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}", "REAL_MV": "/usr/bin/mv",
        "REAL_PYTHON": sys.executable, "PUBLISHED": str(int(published)),
    })
    assert result.returncode == 143, (result.stdout, result.stderr)
    runtime = tmp_path / "agent-home/mcp/single-conversation-subagent"
    assert not list(runtime.glob(".scs-stage.*"))
    python_path = runtime / "python-path"
    if published:
        python = Path(python_path.read_text().strip())
        assert python.is_file()
        assert list(runtime.glob("venv.*")) == [python.parent.parent]
        assert install(tmp_path, "--mode", "hybrid", "--check", env={"REAL_PYTHON": sys.executable}).returncode == 0
    else:
        assert not python_path.exists()
        assert not list(runtime.glob("venv.*"))


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


GPT_MODEL = "new-provider-2/gpt-6.2-sol"
DEEPSEEK_MODEL = "new-provider-2/deepseek-v4.1-flash"
READ_ONLY_TOOLS = "tools: [Read, Grep, Glob]"
INHERITED_PROFILES = {
    "实施工程师": "tools: [Read, Grep, Glob, Edit, Write]",
    "继承审阅员": READ_ONLY_TOOLS,
}
GPT_PROFILES = {"方案顾问": READ_ONLY_TOOLS, "固定审阅员": READ_ONLY_TOOLS}
DEEPSEEK_PROFILES = {
    "查询侦察员": READ_ONLY_TOOLS,
    "测试验证员": "tools: [Read, Grep, Glob, Bash]",
}


def assert_profile(path, model, tools, thought=None):
    text = path.read_text(encoding="utf-8")
    assert "@SCS_" not in text
    header = text.split("---", 2)[1].strip().splitlines()
    assert [line for line in header if line.startswith("name:")] == [f'name: "{path.stem}"']
    assert [line for line in header if line.startswith("model:")] == [f'model: "{model}"']
    assert [line for line in header if line.startswith("tools:")] == [tools]
    assert [line for line in header if line.startswith("thoughtLevel:")] == (
        [f'thoughtLevel: "{thought}"'] if thought else [])


def test_zcode_profiles_opt_in_default_dir_and_no_implicit_fixed_profiles(tmp_path):
    zcode = tmp_path / "fake-home/.zcode"
    config = zcode / "cli/config.json"
    config.parent.mkdir(parents=True)
    config.write_text('{"keep": true}')
    profiles = zcode / "agents"
    assert install(tmp_path).returncode == 0
    assert not profiles.exists()
    assert install(tmp_path, "--check").returncode == 0
    assert not profiles.exists()
    result = install(tmp_path, "--with-zcode-agents")
    assert result.returncode == 0, result.stderr
    assert {p.name for p in profiles.iterdir()} == {f"{name}.md" for name in INHERITED_PROFILES}
    for name, tools in INHERITED_PROFILES.items():
        assert_profile(profiles / f"{name}.md", "inherit", tools)
    assert config.read_text() == '{"keep": true}'
    assert install(tmp_path, "--uninstall").returncode == 0
    assert {p.name for p in profiles.iterdir()} == {f"{name}.md" for name in INHERITED_PROFILES}
    assert config.read_text() == '{"keep": true}'


@pytest.mark.parametrize("use_gpt,use_deepseek,gpt_thought,deepseek_thought", [
    pytest.param(True, False, None, None, id="gpt-only"),
    pytest.param(False, True, None, None, id="deepseek-only"),
    pytest.param(True, True, "enabled", "high", id="both-with-levels"),
])
def test_zcode_profiles_explicit_fixed_models_and_custom_directory(
    tmp_path, use_gpt, use_deepseek, gpt_thought, deepseek_thought,
):
    profiles = tmp_path / "isolated storage"
    args = ["--with-zcode-agents", "--zcode-agents-dir", profiles.name]
    expected = {name: ("inherit", tools, None) for name, tools in INHERITED_PROFILES.items()}
    for selected, family, model, tools_by_name, thought in (
        (use_gpt, "gpt", GPT_MODEL, GPT_PROFILES, gpt_thought),
        (use_deepseek, "deepseek", DEEPSEEK_MODEL, DEEPSEEK_PROFILES, deepseek_thought),
    ):
        if selected:
            args.extend([f"--zcode-{family}-model", model])
            if thought:
                args.extend([f"--zcode-{family}-thought-level", thought])
            expected.update({name: (model, tools, thought) for name, tools in tools_by_name.items()})
    result = install(tmp_path, *args)
    assert result.returncode == 0, result.stderr
    assert not (tmp_path / "fake-home/.zcode").exists()
    assert {p.name for p in profiles.iterdir()} == {f"{name}.md" for name in expected}
    for name, fields in expected.items():
        assert_profile(profiles / f"{name}.md", *fields)


def test_zcode_profiles_reinstall_preserves_existing_models_and_levels(tmp_path):
    args = ("--with-zcode-agents", "--zcode-agents-dir", "profiles")
    result = install(tmp_path, *args,
                     "--zcode-gpt-model", GPT_MODEL, "--zcode-gpt-thought-level", "enabled",
                     "--zcode-deepseek-model", DEEPSEEK_MODEL, "--zcode-deepseek-thought-level", "high")
    assert result.returncode == 0, result.stderr
    profiles = tmp_path / "profiles"
    before = {p.name: p.read_bytes() for p in profiles.iterdir()}
    rerun = install(tmp_path, *args,
                    "--zcode-gpt-model", "other/gpt-example", "--zcode-gpt-thought-level", "low",
                    "--zcode-deepseek-model", "other/deepseek-example", "--zcode-deepseek-thought-level", "low")
    assert rerun.returncode == 0, rerun.stderr
    assert {p.name: p.read_bytes() for p in profiles.iterdir()} == before
    assert install(tmp_path, "--uninstall").returncode == 0
    assert {p.name: p.read_bytes() for p in profiles.iterdir()} == before


def test_zcode_profiles_preserve_existing_paths_and_links(tmp_path):
    profiles = tmp_path / "profiles"
    profiles.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (profiles / "查询侦察员.md").write_text("user configuration")
    (profiles / "实施工程师.md").symlink_to(outside, target_is_directory=True)
    missing = outside / "missing.md"
    (profiles / "测试验证员.md").symlink_to(missing)
    (profiles / "固定审阅员.md").mkdir()
    (profiles / "scout.md").write_text("legacy configuration")
    result = install(tmp_path, "--with-zcode-agents", "--zcode-agents-dir", str(profiles),
                     "--zcode-gpt-model", GPT_MODEL, "--zcode-deepseek-model", DEEPSEEK_MODEL)
    assert result.returncode == 0, result.stderr
    assert (profiles / "查询侦察员.md").read_text() == "user configuration"
    assert (profiles / "实施工程师.md").readlink() == outside
    assert (profiles / "测试验证员.md").readlink() == missing
    assert list(outside.iterdir()) == []
    assert list((profiles / "固定审阅员.md").iterdir()) == []
    assert (profiles / "scout.md").read_text() == "legacy configuration"
    assert (profiles / "继承审阅员.md").is_file()
    assert (profiles / "方案顾问.md").is_file()
    assert not list(profiles.glob(".scs-profile.*"))


@pytest.mark.parametrize("suffix", ["/", "/agents"])
def test_zcode_profiles_reject_symlinked_directory_before_install(tmp_path, suffix):
    outside = tmp_path / "outside"
    outside.mkdir()
    link = tmp_path / "link"
    link.symlink_to(outside, target_is_directory=True)
    result = install(tmp_path, "--with-zcode-agents", "--zcode-agents-dir", str(link) + suffix)
    assert result.returncode != 0
    assert "symlinked installation path" in result.stderr
    assert list(outside.iterdir()) == []
    assert not (tmp_path / "agent-home").exists()


@pytest.mark.parametrize("managed", ["./skills/single-conversation-subagent/profiles",
                                    "./mcp/single-conversation-subagent/profiles"])
def test_zcode_profiles_reject_replaceable_install_storage(tmp_path, managed):
    home = tmp_path / "agent-home"
    result = install(tmp_path, "--with-zcode-agents", "--zcode-agents-dir", f"{home}/{managed}")
    assert result.returncode != 0
    assert "inside the managed Skill or runtime" in result.stderr
    assert not home.exists()


@pytest.mark.parametrize("args", [
    ("--zcode-agents-dir", "profiles"),
    ("--zcode-deepseek-model", DEEPSEEK_MODEL),
    ("--zcode-deepseek-thought-level", "high"),
    ("--with-zcode-agents", "--zcode-gpt-model", "inherit"),
    ("--with-zcode-agents", "--zcode-gpt-model", 'gpt"\ntools: [Bash]'),
    ("--with-zcode-agents", "--zcode-gpt-thought-level", "enabled"),
    ("--with-zcode-agents", "--zcode-deepseek-model", "InHeRiT"),
    ("--with-zcode-agents", "--zcode-deepseek-model", 'deepseek"\ntools: [Bash]'),
    ("--with-zcode-agents", "--zcode-gpt-model", GPT_MODEL, "--zcode-deepseek-thought-level", "high"),
    ("--with-zcode-agents", "--zcode-deepseek-model", DEEPSEEK_MODEL, "--zcode-deepseek-thought-level", "high: invalid"),
    ("--with-zcode-agents", "--zcode-deepseek-model"),
    ("--with-zcode-agents", "--host", "generic"),
    ("--with-zcode-agents", "--check"),
    ("--with-zcode-agents", "--uninstall"),
    ("--skill", "codex-review"),
])
def test_invalid_profile_options_leave_install_and_home_untouched(tmp_path, args):
    assert install(tmp_path, *args).returncode == 2
    assert list(tmp_path.iterdir()) == []
