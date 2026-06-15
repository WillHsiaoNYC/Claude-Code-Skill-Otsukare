#!/bin/bash
# otsukare installer — copies the skill into your Claude Code user skills dir.
# It does NOT touch your statusline or settings; see the README "Prerequisite"
# section to wire up the rate-limit mirror (the one manual step otsukare needs).
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST="$HOME/.claude/skills/otsukare"

echo "Installing otsukare → $DEST"
mkdir -p "$DEST"
cp -R "$SRC/SKILL.md" "$SRC/scripts" "$DEST/"
chmod +x "$DEST/scripts/otsukare_usage.py"

echo "Running tests…"
( cd "$DEST/scripts" && python3 -m unittest test_otsukare_usage )

cat <<'EOF'

✓ Skill installed.

One required manual step remains — expose your usage to the skill by adding the
mirror line to your statusline. See the README section:
  "⚠️ Prerequisite: expose your limits to your local agent"

Then restart Claude Code so it discovers the skill, and verify with:
  python3 ~/.claude/skills/otsukare/scripts/otsukare_usage.py
EOF
