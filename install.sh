#!/usr/bin/env bash
set -euo pipefail

SKILL_NAME="single-conversation-subagent"
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
SRC_DIR="$SCRIPT_DIR/$SKILL_NAME"
AGENT_HOME="${AGENT_HOME:-$HOME/.agents}"
AGENT_HOME="${AGENT_HOME%/}"
[ -n "$AGENT_HOME" ] || AGENT_HOME="/"
[ "$AGENT_HOME" != "/" ] || {
  echo "Refusing AGENT_HOME=/" >&2
  exit 1
}
DEST_DIR="$AGENT_HOME/skills/$SKILL_NAME"
INSTRUCTIONS_FILE=""
SNIPPET="$SCRIPT_DIR/AGENTS.snippet.md"
BEGIN="<!-- single-conversation-subagent begin -->"
END="<!-- single-conversation-subagent end -->"

usage() {
  cat <<'EOF'
Usage: bash install.sh [options]

Options:
  --with-default-rule   Install the skill and add/update the marked default
                        rule in $AGENT_HOME/AGENTS.md.
  --instructions-file <path>
                        Add/update the marked rule in a specific user-level
                        instruction file. Implies --with-default-rule.
  --uninstall           Remove the installed skill and the marked rule block.
  -h, --help            Show this help.

Environment:
  AGENT_HOME            User-level agent configuration directory.
                        Default: $HOME/.agents
EOF
}

backup_path() {
  local path="$1"
  if [ -e "$path" ]; then
    local backup="${path}.bak.$(date +%Y%m%d-%H%M%S).$$"
    cp -R "$path" "$backup"
    echo "Backup: $backup"
  fi
}

assert_safe_skill_dir() {
  [ -n "$DEST_DIR" ] || { echo "Empty destination" >&2; exit 1; }
  [ "$DEST_DIR" != "/" ] || { echo "Refusing to remove /" >&2; exit 1; }
  case "$DEST_DIR" in
    "$AGENT_HOME"/skills/"$SKILL_NAME") ;;
    *) echo "Refusing unexpected destination: $DEST_DIR" >&2; exit 1 ;;
  esac
}

validate_rule_block() {
  local target="$1"
  local begin_count end_count begin_line end_line
  begin_count=$(grep -cxF "$BEGIN" "$target" || true)
  end_count=$(grep -cxF "$END" "$target" || true)
  if [ "$begin_count" -ne 1 ] || [ "$end_count" -ne 1 ]; then
    echo "Malformed rule block in $target (BEGIN=$begin_count, END=$end_count); not modified." >&2
    return 1
  fi
  begin_line=$(grep -nxF "$BEGIN" "$target" | cut -d: -f1)
  end_line=$(grep -nxF "$END" "$target" | cut -d: -f1)
  if [ "$begin_line" -ge "$end_line" ]; then
    echo "Malformed rule block in $target (END before BEGIN); not modified." >&2
    return 1
  fi
  return 0
}

remove_rule_block() {
  [ -n "$INSTRUCTIONS_FILE" ] || return 0
  [ -f "$INSTRUCTIONS_FILE" ] || return 0
  grep -qxF "$BEGIN" "$INSTRUCTIONS_FILE" || return 0
  validate_rule_block "$INSTRUCTIONS_FILE" || return 1
  backup_path "$INSTRUCTIONS_FILE"
  local tmp
  tmp="$(mktemp "${INSTRUCTIONS_FILE}.tmp.XXXXXX")"
  awk -v begin="$BEGIN" -v end="$END" '
    $0 == begin { skip=1; next }
    $0 == end   { skip=0; next }
    !skip       { print }
  ' "$INSTRUCTIONS_FILE" > "$tmp"
  cat "$tmp" > "$INSTRUCTIONS_FILE"
  rm -f "$tmp"
  echo "Removed default rule from $INSTRUCTIONS_FILE"
}

replace_rule_block() {
  local target="$1" snippet="$2" tmp
  tmp="$(mktemp "${target}.tmp.XXXXXX")"
  awk -v snippet_file="$snippet" -v begin="$BEGIN" -v end="$END" '
    BEGIN {
      while ((getline line < snippet_file) > 0) snip = snip line ORS
      close(snippet_file)
    }
    $0 == begin { printf "%s", snip; skip=1; next }
    $0 == end   { skip=0; next }
    !skip       { print }
  ' "$target" > "$tmp"
  cat "$tmp" > "$target"
  rm -f "$tmp"
}

WITH_RULE=0
UNINSTALL=0
while [ "$#" -gt 0 ]; do
  case "$1" in
    --with-default-rule) WITH_RULE=1 ;;
    --instructions-file)
      [ "$#" -ge 2 ] || {
        echo "Missing value for --instructions-file" >&2
        exit 2
      }
      INSTRUCTIONS_FILE="$2"
      WITH_RULE=1
      shift
      ;;
    --uninstall) UNINSTALL=1 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage; exit 2 ;;
  esac
  shift
done

if [ "$UNINSTALL" -eq 1 ] && [ -z "$INSTRUCTIONS_FILE" ]; then
  INSTRUCTIONS_FILE="$AGENT_HOME/AGENTS.md"
fi
if [ "$WITH_RULE" -eq 1 ] && [ -z "$INSTRUCTIONS_FILE" ]; then
  INSTRUCTIONS_FILE="$AGENT_HOME/AGENTS.md"
fi

if [ "$UNINSTALL" -eq 1 ]; then
  if [ -f "$INSTRUCTIONS_FILE" ] && grep -qxF "$BEGIN" "$INSTRUCTIONS_FILE"; then
    validate_rule_block "$INSTRUCTIONS_FILE" || exit 1
  fi
  if [ -d "$DEST_DIR" ]; then
    assert_safe_skill_dir
    backup_path "$DEST_DIR"
    rm -r -- "$DEST_DIR"
    echo "Removed skill: $DEST_DIR"
  else
    echo "Skill not installed at $DEST_DIR"
  fi
  remove_rule_block || exit 1
  echo "Uninstall complete. Start a new session."
  exit 0
fi

[ -f "$SRC_DIR/SKILL.md" ] || {
  echo "Missing $SRC_DIR/SKILL.md" >&2
  exit 1
}

mkdir -p "$AGENT_HOME/skills"
if [ -e "$DEST_DIR" ]; then
  assert_safe_skill_dir
  backup_path "$DEST_DIR"
  rm -r -- "$DEST_DIR"
fi
cp -R "$SRC_DIR" "$DEST_DIR"
echo "Installed skill: $DEST_DIR"

if [ "$WITH_RULE" -eq 1 ]; then
  [ -f "$SNIPPET" ] || {
    echo "Missing $SNIPPET" >&2
    exit 1
  }
  mkdir -p "$(dirname "$INSTRUCTIONS_FILE")"
  if [ -L "$INSTRUCTIONS_FILE" ]; then
    echo "Refusing to modify symlinked $INSTRUCTIONS_FILE" >&2
    exit 1
  fi
  if [ -f "$INSTRUCTIONS_FILE" ] && grep -qxF "$BEGIN" "$INSTRUCTIONS_FILE"; then
    validate_rule_block "$INSTRUCTIONS_FILE" || exit 1
    backup_path "$INSTRUCTIONS_FILE"
    replace_rule_block "$INSTRUCTIONS_FILE" "$SNIPPET"
    echo "Updated default rule in $INSTRUCTIONS_FILE"
  else
    if [ -f "$INSTRUCTIONS_FILE" ]; then
      backup_path "$INSTRUCTIONS_FILE"
      if [ -s "$INSTRUCTIONS_FILE" ]; then
        printf '\n' >> "$INSTRUCTIONS_FILE"
      fi
    fi
    cat "$SNIPPET" >> "$INSTRUCTIONS_FILE"
    echo "Added default rule to $INSTRUCTIONS_FILE"
  fi
fi

cat <<EOF

Installation complete.
  Skill: $DEST_DIR
  Rule:  $([ "$WITH_RULE" -eq 1 ] && echo "$INSTRUCTIONS_FILE" || echo "not modified (use --with-default-rule or --instructions-file)")
Start a new session so the skill registry and user instruction file are reloaded.
EOF
