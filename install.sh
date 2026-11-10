#!/usr/bin/env bash
set -euo pipefail

SKILL_NAME=single-conversation-subagent
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
BEGIN='<!-- single-conversation-subagent begin -->'
END='<!-- single-conversation-subagent end -->'

usage() {
  cat <<'HELP'
Usage: bash install.sh [options]
  --mode skill|hybrid       Default skill; hybrid adds a local MCP runtime.
  --with-default-rule       Update only this skill's marked block in AGENT_HOME/AGENTS.md.
  --instructions-file PATH  Update the marked block in this file; implies default rule.
  --python PATH             Python 3.11+ for hybrid (or SCS_PYTHON).
  --project-root PATH       Explicit allowed project root in generated MCP examples.
  --check                   Read-only installation/dependency check; never installs.
  --uninstall               Back up/remove this skill, runtime and its marked rule only.
  -h, --help                Show help.
AGENT_HOME defaults to ~/.agents. Existing client MCP configs are never rewritten.
Hybrid may install SDK dependencies into its own venv if the chosen Python lacks them.
HELP
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --mode|--instructions-file|--python|--project-root)
      [ "$#" -ge 2 ] || { echo "Missing option value" >&2; exit 2; }
      case "$1" in
        --mode) MODE="$2" ;;
        --instructions-file) INSTRUCTIONS_FILE="$2"; WITH_RULE=1 ;;
        --python) PYTHON_BIN="$2" ;;
        --project-root) PROJECT_ROOT="$2" ;;
      esac
      shift ;;
    --with-default-rule) WITH_RULE=1 ;;
    --check) CHECK=1 ;;
    --uninstall) UNINSTALL=1 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage; exit 2 ;;
  esac
  shift
done
case "$MODE" in skill|hybrid) ;; *) echo 'Mode must be skill or hybrid' >&2; exit 2 ;; esac
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
for child in src venv launcher.py python-path client-examples.json codex-example.toml; do safe_path "$RUNTIME/$child"; done
[ "$DEST_DIR" != "$SCRIPT_DIR/$SKILL_NAME" ] || { echo "Destination is the source; refusing self-replacement" >&2; exit 1; }
if [ -n "$INSTRUCTIONS_FILE" ]; then safe_path "$INSTRUCTIONS_FILE"; validate_block; fi

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
  for installed in "$DEST_DIR" "$RUNTIME"; do
    if [ -d "$installed" ]; then backup "$installed"; rm -r -- "$installed"; fi
  done
  update_rule
  echo 'Uninstalled this skill/runtime. Client configurations and project .scs state were preserved.'
  exit 0
fi

[ -f "$SCRIPT_DIR/$SKILL_NAME/SKILL.md" ] || { echo 'Skill source missing' >&2; exit 1; }
mkdir -p -- "$AGENT_HOME/skills"
if [ ! -d "$DEST_DIR" ] || ! diff -qr "$SCRIPT_DIR/$SKILL_NAME" "$DEST_DIR" >/dev/null 2>&1; then
  backup "$DEST_DIR"
  if [ -d "$DEST_DIR" ]; then rm -r -- "$DEST_DIR"; fi
  cp -R -- "$SCRIPT_DIR/$SKILL_NAME" "$DEST_DIR"
fi
if [ "$WITH_RULE" -eq 1 ]; then update_rule; fi
echo "Skill installed: $DEST_DIR"

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
  for file in launcher.py python-path client-examples.json codex-example.toml; do safe_path "$RUNTIME/$file"; backup "$RUNTIME/$file"; done
  cat > "$RUNTIME/launcher.py" <<'PY'
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).parent / "src"))
from scs_mcp.__main__ import main
main()
PY
  printf '%s\n' "$PYTHON_BIN" > "$RUNTIME/python-path"
  "$PYTHON_BIN" - "$RUNTIME" "$PYTHON_BIN" "$PROJECT_ROOT" <<'PY'
import json, sys
from pathlib import Path
runtime, python, root = sys.argv[1:]
root = str(Path(root).resolve()) if root else "<ABSOLUTE_PROJECT_ROOT>"
entry = {"command": python, "args": [str(Path(runtime)/"launcher.py"), "--project-root", root]}
(Path(runtime)/"client-examples.json").write_text(json.dumps({"mcpServers": {"single-conversation-subagent": entry}}, indent=2)+"\n")
(Path(runtime)/"codex-example.toml").write_text('[mcp_servers.single-conversation-subagent]\ncommand = '+json.dumps(python)+'\nargs = '+json.dumps(entry["args"])+"\n")
PY
  echo "Hybrid runtime ready: $RUNTIME/launcher.py"
  echo "Copy/review the generated examples; replace project-root placeholder: $RUNTIME/client-examples.json"
  echo "Codex example: $RUNTIME/codex-example.toml"
fi
echo 'Start a new chat to load Skill/rule changes. UI visibility still needs host verification.'
