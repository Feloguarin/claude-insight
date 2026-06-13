#!/usr/bin/env python3
"""
Skill data collector.

Emits Claude Insight's deterministic metrics + a sample of the developer's
prompts as JSON, for Claude Code to analyze. Works whether or not the
claude_insight package is pip-installed — it adds the repo root to sys.path
as a fallback.

Usage:
    python3 collect.py [--dir PATH] [--mock]
"""

import os
import sys

# Make the claude_insight package importable even without `pip install -e .`.
# This script lives at <repo>/.claude/skills/ai-fluency/scripts/collect.py,
# so the repo root is four directories up.
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from claude_insight.cli import main  # noqa: E402

if __name__ == "__main__":
    # Reuse the CLI's --json export path with whatever args were passed through.
    sys.argv = ["claude-insight", "--json"] + sys.argv[1:]
    main()
