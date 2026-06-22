#!/usr/bin/env bash
set -euo pipefail

# Claude Insight — removes the /ai-fluency skill from Claude Code.
# Usage: curl -fsSL https://raw.githubusercontent.com/Feloguarin/claude-insight/main/uninstall.sh | bash
#
# By default, your local data (transcript archive and last report) is kept intact.
# Pass --purge to also remove ~/.claude/insight/ and ~/.claude/insight-archive/.

SKILL_DIR="${HOME}/.claude/skills/ai-fluency"
WORKFLOW_FILE="${HOME}/.claude/workflows/ai-fluency.js"
INSIGHT_DIR="${HOME}/.claude/insight"
ARCHIVE_DIR="${HOME}/.claude/insight-archive"

PURGE=0

usage() {
  echo "Usage: uninstall.sh [--purge] [-h|--help]"
  echo ""
  echo "  --purge     Also delete runtime data: ~/.claude/insight/ and ~/.claude/insight-archive/"
  echo "  -h|--help   Show this help"
}

for arg in "$@"; do
  case "$arg" in
    --purge)    PURGE=1 ;;
    -h|--help)  usage; exit 0 ;;
    *)          echo "❌ Unknown flag: $arg"; echo ""; usage; exit 1 ;;
  esac
done

echo "🗑️  Uninstalling Claude Insight /ai-fluency skill"
echo "================================================="

# --- Installed artifacts (always removed) ---

if [ -d "$SKILL_DIR" ]; then
  rm -rf "$SKILL_DIR"
  echo "✅ Removed skill dir  → $SKILL_DIR"
else
  echo "ℹ️  Skill dir not found (already removed): $SKILL_DIR"
fi

if [ -f "$WORKFLOW_FILE" ]; then
  rm -f "$WORKFLOW_FILE"
  echo "✅ Removed workflow   → $WORKFLOW_FILE"
else
  echo "ℹ️  Workflow file not found (already removed): $WORKFLOW_FILE"
fi

# --- Runtime data (only with --purge) ---

if [ "$PURGE" -eq 1 ]; then
  echo ""
  echo "⚠️  --purge: deleting runtime data and archived transcript history"

  if [ -d "$INSIGHT_DIR" ]; then
    rm -rf "$INSIGHT_DIR"
    echo "✅ Removed insight dir → $INSIGHT_DIR"
  else
    echo "ℹ️  Insight dir not found: $INSIGHT_DIR"
  fi

  if [ -d "$ARCHIVE_DIR" ]; then
    rm -rf "$ARCHIVE_DIR"
    echo "✅ Removed archive     → $ARCHIVE_DIR"
  else
    echo "ℹ️  Archive dir not found: $ARCHIVE_DIR"
  fi

  # Warn about a custom archive location the tool cannot know to delete.
  if [ -n "${CLAUDE_INSIGHT_ARCHIVE:-}" ]; then
    echo ""
    echo "ℹ️  Custom archive detected via \$CLAUDE_INSIGHT_ARCHIVE:"
    echo "   ${CLAUDE_INSIGHT_ARCHIVE}"
    echo "   This location was not removed — delete it manually if you no longer need it."
  fi
else
  echo ""
  echo "ℹ️  Your data was kept (~/.claude/insight/ and ~/.claude/insight-archive/)."
  echo "   Re-run with --purge to also remove them."
fi

echo ""
echo "📝 If you added \"cleanupPeriodDays\" to ~/.claude/settings.json, remove it by hand."
echo ""
echo "👋 Done. Reinstall anytime:"
echo ""
echo "      curl -fsSL https://raw.githubusercontent.com/Feloguarin/claude-insight/main/install.sh | bash"
echo ""
