"""Tests for the Claude Code /ai-fluency skill and its installer."""

import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL_DIR = REPO_ROOT / ".claude" / "skills" / "ai-fluency"
WORKFLOW = REPO_ROOT / ".claude" / "workflows" / "ai-fluency.js"
FRAMEWORK = REPO_ROOT / "reference" / "ai-fluency-framework.md"
INSTALL = REPO_ROOT / "install.sh"


class SkillFilesTests(unittest.TestCase):
    def test_skill_pieces_exist(self):
        self.assertTrue((SKILL_DIR / "SKILL.md").exists())
        self.assertTrue(WORKFLOW.exists(), "the workflow the skill invokes must exist")
        self.assertTrue(FRAMEWORK.exists(), "the framework the workflow reads must exist")
        self.assertTrue((REPO_ROOT / "insight.py").exists(), "the engine the skill runs must exist")

    def test_frontmatter_has_required_fields(self):
        text = (SKILL_DIR / "SKILL.md").read_text()
        match = re.match(r"^---\n(.*?)\n---", text, re.S)
        self.assertIsNotNone(match, "SKILL.md must start with a YAML frontmatter block")
        fm = match.group(1)
        self.assertRegex(fm, r"(?m)^name:\s*ai-fluency\s*$")
        self.assertRegex(fm, r"(?m)^description:\s*\S")
        # The skill must invoke the v2 engine and the bundled two-model workflow.
        self.assertIn("insight.py", text)
        self.assertIn("Workflow", text)


class InstallerTests(unittest.TestCase):
    def test_installer_places_every_skill_piece(self):
        text = INSTALL.read_text()
        for needed in ("insight.py", "ai-fluency-framework.md", "SKILL.md", "ai-fluency.js",
                       ".claude/skills/ai-fluency", ".claude/workflows"):
            self.assertIn(needed, text, f"installer should reference {needed}")


if __name__ == "__main__":
    unittest.main()
