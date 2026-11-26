#!/usr/bin/env bash
set -euo pipefail

SKILL_NAME=single-conversation-subagent
SKILL_SELECT=single-conversation-subagent
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
AGENT_HOME="${AGENT_HOME:-$HOME/.agents}"
AGENT_HOME="${AGENT_HOME%/}"
MODE=skill
WITH_RULE=0
UNINSTALL=0
CHECK=0
INSTRUCTIONS_FILE=""
PYTHON_BIN="${SCS_PYTHON:-python3}"
PROJECT_ROOT=""
ALLOW_ROOTS=()
HOST=generic
QUESTION_CHANNEL=""
WITH_ZCODE_AGENTS=0
ZCODE_AGENTS_DIR=""
ZCODE_GPT_MODEL=""
ZCODE_GPT_THOUGHT_LEVEL=""
ZCODE_DEEPSEEK_MODEL=""
ZCODE_DEEPSEEK_THOUGHT_LEVEL=""
ZCODE_AGENT_OPTIONS=0
ZCODE_PROFILE_NAMES=(scs-implementer scs-inherited-reviewer)
ZCODE_LEGACY_PROFILES=(
  方案顾问:scs-advisor
  固定审阅员:scs-fixed-reviewer
  继承审阅员:scs-inherited-reviewer
  查询侦察员:scs-scout
  实施工程师:scs-implementer
  测试验证员:scs-test-verifier
  设备与端到端验证员:scs-device-verifier
)
BEGIN='<!-- single-conversation-subagent begin -->'
END='<!-- single-conversation-subagent end -->'

usage() {
  cat <<'HELP'
Usage: bash install.sh [options]
  --mode skill|hybrid       Default skill; hybrid adds a local MCP runtime.
  --skill NAME              Bundled skill to install: single-conversation-subagent (default)
                            or codex-review (pure Skill; no AGENTS rule, no MCP runtime).
  --with-default-rule       Update only this skill's marked block in AGENT_HOME/AGENTS.md.
  --instructions-file PATH  Update the marked block in this file; implies default rule.
  --python PATH             Python 3.11+ for hybrid (or SCS_PYTHON).
  --project-root PATH       Default allowed workspace in generated MCP examples.
  --allow-root PATH         Additional allowed workspace; repeatable.
  --host NAME               Host label for examples: codex|zcode|claude|generic.
  --question-channel MODE   auto|native|mcp|text; defaults per host.
  --with-zcode-agents       Opt in to adding missing ZCode agent profiles (ZCode host only).
                            Stops before installation if legacy Chinese filenames remain.
  --zcode-agents-dir PATH   Profile directory; defaults to ~/.zcode/agents.
  --zcode-gpt-model ID      Add scs-advisor and scs-fixed-reviewer with this explicit GPT model ID.
  --zcode-gpt-thought-level LEVEL
                            Optional thoughtLevel for those newly installed GPT profiles.
  --zcode-deepseek-model ID Add scs-scout, scs-test-verifier, and scs-device-verifier
                            with this explicit DeepSeek model ID.
  --zcode-deepseek-thought-level LEVEL
                            Optional thoughtLevel for those newly installed DeepSeek profiles.
                            Fixed profiles are skipped without their model ID; never inherit.
                            Directory/model/thought options require --with-zcode-agents.
  --check                   Read-only installation/dependency check; never installs.
  --uninstall               Back up/remove this skill, runtime and its marked rule only.
  -h, --help                Show help.
AGENT_HOME defaults to ~/.agents. Existing client MCP configs are never rewritten.
Hybrid may install SDK dependencies into its own venv if the chosen Python lacks them.
HELP
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --mode|--instructions-file|--python|--project-root|--allow-root|--host|--question-channel|--skill|--zcode-agents-dir|--zcode-gpt-model|--zcode-gpt-thought-level|--zcode-deepseek-model|--zcode-deepseek-thought-level)
      [ "$#" -ge 2 ] || { echo "Missing option value" >&2; exit 2; }
      case "$1" in
        --mode) MODE="$2" ;;
        --skill) SKILL_SELECT="$2" ;;
        --instructions-file) INSTRUCTIONS_FILE="$2"; WITH_RULE=1 ;;
        --python) PYTHON_BIN="$2" ;;
        --project-root) PROJECT_ROOT="$2" ;;
        --allow-root) ALLOW_ROOTS+=("$2") ;;
        --host) HOST="$2" ;;
        --question-channel) QUESTION_CHANNEL="$2" ;;
        --zcode-agents-dir) ZCODE_AGENTS_DIR="$2"; ZCODE_AGENT_OPTIONS=1 ;;
        --zcode-gpt-model) ZCODE_GPT_MODEL="$2"; ZCODE_AGENT_OPTIONS=1 ;;
        --zcode-gpt-thought-level) ZCODE_GPT_THOUGHT_LEVEL="$2"; ZCODE_AGENT_OPTIONS=1 ;;
        --zcode-deepseek-model) ZCODE_DEEPSEEK_MODEL="$2"; ZCODE_AGENT_OPTIONS=1 ;;
        --zcode-deepseek-thought-level) ZCODE_DEEPSEEK_THOUGHT_LEVEL="$2"; ZCODE_AGENT_OPTIONS=1 ;;
      esac
      shift ;;
    --with-default-rule) WITH_RULE=1 ;;
    --with-zcode-agents) WITH_ZCODE_AGENTS=1 ;;
    --check) CHECK=1 ;;
    --uninstall) UNINSTALL=1 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage; exit 2 ;;
  esac
  shift
done
case "$MODE" in skill|hybrid) ;; *) echo 'Mode must be skill or hybrid' >&2; exit 2 ;; esac
case "$HOST" in codex|zcode|claude|generic) ;; *) echo 'Host must be codex, zcode, claude or generic' >&2; exit 2 ;; esac
case "$SKILL_SELECT" in
  single-conversation-subagent|codex-review) SKILL_NAME="$SKILL_SELECT" ;;
  *) echo "Unknown skill: $SKILL_SELECT" >&2; exit 2 ;;
esac
if [ "$SKILL_NAME" != single-conversation-subagent ]; then
  if [ "$WITH_RULE" -eq 1 ]; then
    echo "Note: $SKILL_NAME has no AGENTS rule block; installing Skill only." >&2
  fi
  MODE=skill
  WITH_RULE=0
  INSTRUCTIONS_FILE=""
fi
if [ "$ZCODE_AGENT_OPTIONS" -eq 1 ] && [ "$WITH_ZCODE_AGENTS" -eq 0 ]; then
  echo 'ZCode profile options require --with-zcode-agents.' >&2; exit 2
fi
# Validate YAML scalar values before expanding fixed-model templates.
validate_zcode_model_options() {
  local family="$1" model="$2" thought="$3"
  if [ -n "$model" ]; then
    if [[ ! "$model" =~ ^[A-Za-z0-9][A-Za-z0-9._:+@-]*/[A-Za-z0-9][A-Za-z0-9._:+@-]*$ || "$model" =~ ^[Ii][Nn][Hh][Ee][Rr][Ii][Tt]$ ]]; then
      printf '%s\n' "--zcode-$family-model must be an explicit provider/model ID, not inherit." >&2; exit 2
    fi
  elif [ -n "$thought" ]; then
    printf '%s\n' "--zcode-$family-thought-level requires --zcode-$family-model." >&2; exit 2
  fi
  if [ -n "$thought" ] && [[ ! "$thought" =~ ^[A-Za-z0-9][A-Za-z0-9_-]*$ ]]; then
    printf '%s\n' "--zcode-$family-thought-level must be a simple level name supported by the model." >&2; exit 2
  fi
}
if [ "$WITH_ZCODE_AGENTS" -eq 1 ]; then
  if [ "$HOST" != zcode ] || [ "$SKILL_NAME" != single-conversation-subagent ] || [ "$CHECK" -eq 1 ] || [ "$UNINSTALL" -eq 1 ]; then
    echo '--with-zcode-agents requires the single-conversation-subagent Skill and --host zcode; unavailable with --check or --uninstall.' >&2; exit 2
  fi
  ZCODE_AGENTS_DIR="${ZCODE_AGENTS_DIR:-$HOME/.zcode/agents}"
  case "$ZCODE_AGENTS_DIR" in /*) ;; *) ZCODE_AGENTS_DIR="$PWD/$ZCODE_AGENTS_DIR" ;; esac
  validate_zcode_model_options gpt "$ZCODE_GPT_MODEL" "$ZCODE_GPT_THOUGHT_LEVEL"
  validate_zcode_model_options deepseek "$ZCODE_DEEPSEEK_MODEL" "$ZCODE_DEEPSEEK_THOUGHT_LEVEL"
  if [ -n "$ZCODE_GPT_MODEL" ]; then ZCODE_PROFILE_NAMES+=(scs-advisor scs-fixed-reviewer); fi
  if [ -n "$ZCODE_DEEPSEEK_MODEL" ]; then ZCODE_PROFILE_NAMES+=(scs-scout scs-test-verifier scs-device-verifier); fi
fi
if [ -z "$QUESTION_CHANNEL" ]; then
  case "$HOST" in
    codex) QUESTION_CHANNEL=native ;;
    zcode) QUESTION_CHANNEL=native ;;
    *) QUESTION_CHANNEL=auto ;;
  esac
fi
case "$QUESTION_CHANNEL" in auto|native|mcp|text) ;; *) echo 'Question channel must be auto, native, mcp or text' >&2; exit 2 ;; esac
[ -n "$AGENT_HOME" ] && [ "$AGENT_HOME" != / ] || { echo 'Refusing empty/root AGENT_HOME' >&2; exit 1; }
case "$AGENT_HOME" in /*) ;; *) AGENT_HOME="$PWD/$AGENT_HOME" ;; esac
DEST_DIR="$AGENT_HOME/skills/$SKILL_NAME"
RUNTIME="$AGENT_HOME/mcp/$SKILL_NAME"
if { [ "$WITH_RULE" -eq 1 ] || [ "$UNINSTALL" -eq 1 ]; } && [ -z "$INSTRUCTIONS_FILE" ]; then
  INSTRUCTIONS_FILE="$AGENT_HOME/AGENTS.md"
fi

safe_path() {
  local target="$1" component
  case "/$target/" in */../*) echo 'Refusing parent navigation' >&2; return 1 ;; esac
  case "$target" in /*) ;; *) target="$PWD/$target" ;; esac
  component="$target"
  while [ "$component" != / ] && [ -n "$component" ]; do
    [ ! -L "$component" ] || { echo "Refusing symlinked installation path: $component" >&2; return 1; }
    component="$(dirname -- "$component")"
  done
}
backup() {
  if [ -e "$1" ]; then
    cp -pR -- "$1" "$1.bak.$(date +%Y%m%d-%H%M%S).$$"
    echo "Backed up: $1"
  fi
}
backup_skill() {
  if [ "$HOST" = zcode ] && [ -d "$DEST_DIR" ]; then
    local archive
    mkdir -p -- "$AGENT_HOME/.scs-backups"
    archive="$(mktemp -d "$AGENT_HOME/.scs-backups/$SKILL_NAME.XXXXXX")"
    cp -pR -- "$DEST_DIR" "$archive/$SKILL_NAME"
    echo "Backed up: $DEST_DIR -> $archive/$SKILL_NAME"
  else
    backup "$DEST_DIR"
  fi
}
validate_block() {
  local b e bl el
  [ -f "$INSTRUCTIONS_FILE" ] || return 0
  b=$(grep -cxF "$BEGIN" "$INSTRUCTIONS_FILE" || true)
  e=$(grep -cxF "$END" "$INSTRUCTIONS_FILE" || true)
  if [ "$b" -eq 0 ] && [ "$e" -eq 0 ]; then return 0; fi
  [ "$b" -eq 1 ] && [ "$e" -eq 1 ] || { echo 'Malformed marked rule; nothing modified.' >&2; return 1; }
  bl=$(grep -nxF "$BEGIN" "$INSTRUCTIONS_FILE" | cut -d: -f1)
  el=$(grep -nxF "$END" "$INSTRUCTIONS_FILE" | cut -d: -f1)
  [ "$bl" -lt "$el" ] || { echo 'Reversed marked rule; nothing modified.' >&2; return 1; }
}
# All destination checks precede mutations, including skill-only installation.
safe_path "$DEST_DIR"
safe_path "$RUNTIME"
if [ "$HOST" = zcode ]; then safe_path "$AGENT_HOME/.scs-backups"; fi
for child in src venv launcher.py python-path client-examples.json codex-example.toml zcode-example.json; do safe_path "$RUNTIME/$child"; done
[ "$DEST_DIR" != "$SCRIPT_DIR/$SKILL_NAME" ] || { echo "Destination is the source; refusing self-replacement" >&2; exit 1; }
if [ -n "$INSTRUCTIONS_FILE" ]; then safe_path "$INSTRUCTIONS_FILE"; validate_block; fi

zcode_profile_template() {
  case "$1" in
    scs-advisor|scs-fixed-reviewer|scs-scout|scs-test-verifier|scs-device-verifier) printf '%s\n' "$SCRIPT_DIR/zcode-agents/$1.md.in" ;;
    *) printf '%s\n' "$SCRIPT_DIR/zcode-agents/$1.md" ;;
  esac
}
# Used only after safe_path rejects '..' and symlink components. Normalize aliases
# like '/./' so a custom profile store cannot live in a replaceable install tree.
normalize_install_path() {
  local rest="${1#/}/" part normalized=""
  while [ -n "$rest" ]; do
    part="${rest%%/*}"
    rest="${rest#*/}"
    case "$part" in ''|.) ;; *) normalized="$normalized/$part" ;; esac
  done
  printf '%s\n' "${normalized:-/}"
}
if [ "$WITH_ZCODE_AGENTS" -eq 1 ]; then
  while [[ "$ZCODE_AGENTS_DIR" == */ && "$ZCODE_AGENTS_DIR" != / ]]; do
    ZCODE_AGENTS_DIR="${ZCODE_AGENTS_DIR%/}"
  done
  safe_path "$ZCODE_AGENTS_DIR"
  ZCODE_AGENTS_DIR="$(normalize_install_path "$ZCODE_AGENTS_DIR")"
  case "$ZCODE_AGENTS_DIR/" in
    "$(normalize_install_path "$DEST_DIR")/"*|"$(normalize_install_path "$RUNTIME")/"*|"$(normalize_install_path "$AGENT_HOME/.scs-backups")/"*)
      echo 'Refusing ZCode profiles directory inside managed installation storage.' >&2; exit 1 ;;
  esac
  if [ "$ZCODE_AGENTS_DIR" = / ] || [ "$ZCODE_AGENTS_DIR" -ef "$SCRIPT_DIR/zcode-agents" ] ||
     { [ -e "$ZCODE_AGENTS_DIR" ] && [ ! -d "$ZCODE_AGENTS_DIR" ]; }; then
    echo 'Refusing unsafe ZCode profiles directory.' >&2; exit 1
  fi
  # Check every legacy name before any install writes, even when its role is
  # not selected by the model flags or its replacement already exists.
  legacy_found=0
  for mapping in "${ZCODE_LEGACY_PROFILES[@]}"; do
    legacy_path="$ZCODE_AGENTS_DIR/${mapping%:*}.md"
    if [ -e "$legacy_path" ] || [ -L "$legacy_path" ]; then
      printf 'Legacy ZCode profile: %s -> %s.md (name: "%s")\n' \
        "$legacy_path" "${mapping#*:}" "${mapping#*:}" >&2
      legacy_found=1
    fi
  done
  if [ "$legacy_found" -eq 1 ]; then
    printf '%s\n' \
      'Installation stopped before making changes. Back up legacy profiles outside the agents directory.' \
      'Rename each legacy file and update only its frontmatter name; keep its model, thoughtLevel, tools, and prompt.' \
      'If the new filename already exists, compare both profiles and resolve the conflict manually without overwriting either file.' \
      "See $SCRIPT_DIR/zcode-agents/profiles/README.md for migration steps, then rerun the installer." >&2
    exit 1
  fi
  for name in "${ZCODE_PROFILE_NAMES[@]}"; do
    [ -f "$(zcode_profile_template "$name")" ] || { echo "Missing ZCode profile template: $name" >&2; exit 1; }
  done
fi

install_zcode_agents() {
  local name destination stage template model thought
  mkdir -p -- "$ZCODE_AGENTS_DIR"
  safe_path "$ZCODE_AGENTS_DIR"
  for name in "${ZCODE_PROFILE_NAMES[@]}"; do
    destination="$ZCODE_AGENTS_DIR/$name.md"
    if [ -e "$destination" ] || [ -L "$destination" ]; then
      printf 'Preserved existing ZCode profile path: %s\n' "$destination"
      continue
    fi
    template="$(zcode_profile_template "$name")"
    case "$name" in
      scs-advisor|scs-fixed-reviewer) model="$ZCODE_GPT_MODEL"; thought="$ZCODE_GPT_THOUGHT_LEVEL" ;;
      scs-scout|scs-test-verifier|scs-device-verifier) model="$ZCODE_DEEPSEEK_MODEL"; thought="$ZCODE_DEEPSEEK_THOUGHT_LEVEL" ;;
    esac
    stage="$(mktemp "$ZCODE_AGENTS_DIR/.scs-profile.XXXXXX")"
    if [[ "$template" == *.md.in ]]; then
      if ! awk -v model="$model" -v thought="$thought" '
        $0 == "@SCS_MODEL@" { printf "model: \"%s\"\n", model; next }
        $0 == "@SCS_THOUGHT_LEVEL@" { if (thought != "") printf "thoughtLevel: \"%s\"\n", thought; next }
        { print }
      ' "$template" > "$stage"; then
        rm -f -- "$stage"; return 1
      fi
    elif ! cp -- "$template" "$stage"; then
      rm -f -- "$stage"; return 1
    fi
    if ! chmod =rw,go-w -- "$stage"; then
      rm -f -- "$stage"; return 1
    fi
    # Publish atomically without replacing or following the destination leaf.
    if ln -T -- "$stage" "$destination" 2>/dev/null; then
      printf 'ZCode profile installed: %s\n' "$destination"
    elif [ -e "$destination" ] || [ -L "$destination" ]; then
      printf 'Preserved existing ZCode profile path: %s\n' "$destination"
    else
      rm -f -- "$stage"
      printf 'Cannot create ZCode profile path: %s (requires hard links and ln -T)\n' "$destination" >&2
      return 1
    fi
    rm -f -- "$stage"
    stage=""
  done
  if [ -z "$ZCODE_GPT_MODEL" ]; then
    echo 'Not installing new scs-advisor or scs-fixed-reviewer profiles: use --zcode-gpt-model with an explicit ZCode model ID.'
  fi
  if [ -z "$ZCODE_DEEPSEEK_MODEL" ]; then
    echo 'Not installing new scs-scout, scs-test-verifier, or scs-device-verifier profiles: use --zcode-deepseek-model with an explicit ZCode model ID.'
  fi
}

sdk_ready() {
  "$PYTHON_BIN" -c 'import sys,importlib.metadata; assert sys.version_info >= (3,11); assert importlib.metadata.version("mcp") == "2.3.0"; from mcp.server import MCPServer; from mcp.server.mcpserver import Resolve,Elicit' >/dev/null 2>&1
}
if [ "$CHECK" -eq 1 ]; then
  [ -f "$DEST_DIR/SKILL.md" ] || { echo 'SKILL_NOT_LOADED: installation missing; registry loading still needs host evidence.' >&2; exit 1; }
  echo 'Skill files exist. Host registry/loading: NOT_TESTED.'
  if [ "$MODE" = hybrid ]; then
    [ -f "$RUNTIME/launcher.py" ] || { echo 'MCP runtime missing' >&2; exit 1; }
    if [ -f "$RUNTIME/python-path" ]; then PYTHON_BIN="$(cat "$RUNTIME/python-path")"; fi
    sdk_ready || { echo 'MCP SDK/Python check failed; pure Skill remains usable.' >&2; exit 1; }
    "$PYTHON_BIN" "$RUNTIME/launcher.py" --version
    echo 'MCP imports pass. Client connection/UI: NOT_TESTED.'
  fi
  exit 0
fi

update_rule() {
  [ -n "$INSTRUCTIONS_FILE" ] || return 0
  if [ "$UNINSTALL" -eq 1 ] && [ ! -f "$INSTRUCTIONS_FILE" ]; then return 0; fi
  mkdir -p -- "$(dirname -- "$INSTRUCTIONS_FILE")"
  local tmp
  tmp="$(mktemp "${INSTRUCTIONS_FILE}.tmp.XXXXXX")"
  if [ -f "$INSTRUCTIONS_FILE" ] && grep -qxF "$BEGIN" "$INSTRUCTIONS_FILE"; then
    awk -v begin="$BEGIN" -v end="$END" -v uninstall="$UNINSTALL" -v snippet="$SCRIPT_DIR/AGENTS.snippet.md" '
      $0==begin {if (!uninstall) {while ((getline s < snippet)>0) print s; close(snippet)} skip=1; next}
      $0==end {skip=0; next} !skip {print}
    ' "$INSTRUCTIONS_FILE" > "$tmp"
  elif [ "$UNINSTALL" -eq 0 ]; then
    if [ -f "$INSTRUCTIONS_FILE" ]; then cat "$INSTRUCTIONS_FILE" > "$tmp"; printf '\n' >> "$tmp"; fi
    cat "$SCRIPT_DIR/AGENTS.snippet.md" >> "$tmp"
  else
    rm -- "$tmp"; return 0
  fi
  if [ -f "$INSTRUCTIONS_FILE" ] && cmp -s "$tmp" "$INSTRUCTIONS_FILE"; then rm -- "$tmp"; return 0; fi
  backup "$INSTRUCTIONS_FILE"
  # Preserve existing permissions; create private permissions on a new file.
  if [ -f "$INSTRUCTIONS_FILE" ]; then cat "$tmp" > "$INSTRUCTIONS_FILE"; rm -- "$tmp"; else mv -- "$tmp" "$INSTRUCTIONS_FILE"; fi
}
if [ "$UNINSTALL" -eq 1 ]; then
  if [ -d "$DEST_DIR" ]; then
    backup_skill
    rm -r -- "$DEST_DIR"
  fi
  if [ -d "$RUNTIME" ]; then backup "$RUNTIME"; rm -r -- "$RUNTIME"; fi
  update_rule
  echo 'Uninstalled this skill/runtime. Client configurations and project .scs state were preserved.'
  exit 0
fi

[ -f "$SCRIPT_DIR/$SKILL_NAME/SKILL.md" ] || { echo 'Skill source missing' >&2; exit 1; }
mkdir -p -- "$AGENT_HOME/skills"
if [ ! -d "$DEST_DIR" ] || ! diff -qr "$SCRIPT_DIR/$SKILL_NAME" "$DEST_DIR" >/dev/null 2>&1; then
  if [ -d "$DEST_DIR" ]; then
    backup_skill
    rm -r -- "$DEST_DIR"
  fi
  cp -R -- "$SCRIPT_DIR/$SKILL_NAME" "$DEST_DIR"
fi
if [ "$WITH_RULE" -eq 1 ]; then update_rule; fi
echo "Skill installed: $DEST_DIR"
if [ "$WITH_ZCODE_AGENTS" -eq 1 ]; then install_zcode_agents; fi

if [ "$MODE" = hybrid ]; then
  if ! command -v "$PYTHON_BIN" >/dev/null 2>&1 || ! "$PYTHON_BIN" -c 'import sys; assert sys.version_info >= (3,11)' >/dev/null 2>&1; then
    echo 'HYBRID_UNAVAILABLE: Python 3.11+ missing. Skill installation succeeded; use text fallback.' >&2
    exit 0
  fi
  mkdir -p -- "$RUNTIME"
  if ! sdk_ready; then
    # Failed bootstrap never damages the installed Skill; old runtime is retained.
    bootstrap_env="$(mktemp -d "$RUNTIME/venv.XXXXXX")"
    if ! "$PYTHON_BIN" -m venv "$bootstrap_env" || ! "$bootstrap_env/bin/python" -m pip install 'mcp==2.3.0' 'pydantic>=2.12,<3'; then
      rm -r -- "$bootstrap_env"
      echo 'HYBRID_UNAVAILABLE: dependencies failed. Pure Skill and previous runtime remain installed.' >&2
      exit 0
    fi
    PYTHON_BIN="$bootstrap_env/bin/python"
  fi
  PYTHON_BIN="$("$PYTHON_BIN" -c 'import sys; print(sys.executable)')"
  if [ -d "$RUNTIME/src" ] && ! diff -qr -x __pycache__ -x '*.pyc' "$SCRIPT_DIR/mcp_server/src" "$RUNTIME/src" >/dev/null 2>&1; then
    backup "$RUNTIME/src"; rm -r -- "$RUNTIME/src"
  fi
  if [ ! -d "$RUNTIME/src" ]; then
    mkdir -p -- "$RUNTIME/src"
    tar -C "$SCRIPT_DIR/mcp_server/src" --exclude=__pycache__ --exclude='*.pyc' -cf - . | tar -C "$RUNTIME/src" -xf -
  fi
  # Never change arbitrary client configs. Back up our own generated files too.
  runtime_files=(launcher.py python-path client-examples.json codex-example.toml host-profile.json)
  if [ "$HOST" = zcode ]; then runtime_files+=(zcode-example.json); fi
  for file in "${runtime_files[@]}"; do safe_path "$RUNTIME/$file"; backup "$RUNTIME/$file"; done
  cat > "$RUNTIME/launcher.py" <<'PY'
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).parent / "src"))
from scs_mcp.__main__ import main
main()
PY
  printf '%s\n' "$PYTHON_BIN" > "$RUNTIME/python-path"
  "$PYTHON_BIN" - "$RUNTIME" "$PYTHON_BIN" "$PROJECT_ROOT" "$HOST" "$QUESTION_CHANNEL" "${ALLOW_ROOTS[@]}" <<'PY'
import json, sys
from pathlib import Path
runtime, python, root, host, channel, *extra_roots = sys.argv[1:]
root = str(Path(root).resolve()) if root else "<ABSOLUTE_PROJECT_ROOT>"
env = {"SCS_HOST": host, "SCS_QUESTION_CHANNEL": channel}
if host == "zcode":
    env["SCS_NATIVE_REVIEW"] = "1"
args = [str(Path(runtime)/"launcher.py"), "--project-root", root]
for extra in extra_roots:
    args.extend(["--allow-root", str(Path(extra).resolve())])
entry = {"command": python, "args": args, "env": env}
(Path(runtime)/"client-examples.json").write_text(json.dumps({"mcpServers": {"single-conversation-subagent": entry}}, indent=2)+"\n")
(Path(runtime)/"codex-example.toml").write_text('[mcp_servers.single-conversation-subagent]\ncommand = '+json.dumps(python)+'\nargs = '+json.dumps(entry["args"])+'\n[mcp_servers.single-conversation-subagent.env]\nSCS_HOST = '+json.dumps(host)+'\nSCS_QUESTION_CHANNEL = '+json.dumps(channel)+"\n")
(Path(runtime)/"host-profile.json").write_text(json.dumps({"host": host, "question_channel": channel}, indent=2)+"\n")
if host == "zcode":
    example = {"mcp": {"servers": {"single-conversation-subagent": {"type": "stdio", **entry, "timeoutMs": 60000}}}}
    (Path(runtime)/"zcode-example.json").write_text(json.dumps(example, indent=2)+"\n")
PY
  echo "Hybrid runtime ready: $RUNTIME/launcher.py"
  echo "Question channel: $HOST / $QUESTION_CHANNEL"
  echo "Copy/review the generated examples; replace project-root placeholder: $RUNTIME/client-examples.json"
  if [ "$HOST" = zcode ]; then
    echo "ZCode example: $RUNTIME/zcode-example.json"
  else
    echo "Codex example: $RUNTIME/codex-example.toml"
  fi
fi
echo 'Start a new chat to load Skill/rule changes. UI visibility still needs host verification.'
