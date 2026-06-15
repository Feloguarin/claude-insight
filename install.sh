#!/usr/bin/env bash
set -euo pipefail

# Claude Insight — installs the /ai-fluency skill into Claude Code.
# Usage: curl -fsSL https://raw.githubusercontent.com/Feloguarin/claude-insight/main/install.sh | bash
#
# After this, open Claude Code in any folder and run:  /ai-fluency

REPO="Feloguarin/claude-insight"
BRANCH="main"
SKILL_DIR="${HOME}/.claude/skills/ai-fluency"
WORKFLOW_DIR="${HOME}/.claude/workflows"

echo "🔍 Installing the Claude Insight /ai-fluency skill"
echo "=================================================="

# Python 3 is required — the skill runs the bundled engine with it.
if ! command -v python3 >/dev/null 2>&1; then
  echo "❌ Python 3 is required but was not found. Install Python 3.8+ and re-run."
  exit 1
fi
echo "✅ python3 found"

# Download the repo into a temp dir (tarball — no git or pip needed).
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
echo "📥 Downloading…"
if ! curl -fsSL "https://github.com/${REPO}/archive/refs/heads/${BRANCH}.tar.gz" | tar -xz -C "$TMP"; then
  echo "❌ Download failed. Check your connection and try again."
  exit 1
fi
SRC="${TMP}/claude-insight-${BRANCH}"

# Install the skill self-contained: the engine and the framework live next to SKILL.md,
# and the workflow goes where Claude Code looks for workflows.
mkdir -p "$SKILL_DIR/reference" "$WORKFLOW_DIR"
cp "$SRC/insight.py"                          "$SKILL_DIR/insight.py"
cp "$SRC/reference/ai-fluency-framework.md"   "$SKILL_DIR/reference/ai-fluency-framework.md"
cp "$SRC/.claude/skills/ai-fluency/SKILL.md"  "$SKILL_DIR/SKILL.md"
cp "$SRC/.claude/workflows/ai-fluency.js"     "$WORKFLOW_DIR/ai-fluency.js"

echo "✅ Installed:"
echo "   • skill    → $SKILL_DIR"
echo "   • workflow → $WORKFLOW_DIR/ai-fluency.js"
echo ""
echo "🎉 Done. Open Claude Code in any folder and run:"
echo ""
echo "      /ai-fluency"
echo ""
echo "   The report lands in ~/.claude/insight/ai_fluency_report.html"
