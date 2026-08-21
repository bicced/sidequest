#!/usr/bin/env bash
# Install the sidequest skill and subagent into ~/.claude/
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST="${CLAUDE_HOME:-$HOME/.claude}"

mkdir -p "$DEST/skills/sidequest" "$DEST/agents"
cp "$HERE/skills/sidequest/SKILL.md" "$DEST/skills/sidequest/SKILL.md"
cp "$HERE/agents/sidequest.md"       "$DEST/agents/sidequest.md"

echo "installed:"
echo "  $DEST/skills/sidequest/SKILL.md"
echo "  $DEST/agents/sidequest.md"
echo
if ! command -v sidequest >/dev/null 2>&1; then
  echo "note: the 'sidequest' command is not on PATH yet."
  echo "      pip install sidequest    (or: uv tool install sidequest)"
fi
if [ -z "${NANOGPT_API_KEY:-}" ]; then
  echo "note: NANOGPT_API_KEY is not set. Get a key at https://nano-gpt.com"
fi
