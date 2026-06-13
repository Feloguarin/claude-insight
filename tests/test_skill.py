"""Tests for the Claude Code skill: its data collector and SKILL.md."""

import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL_DIR = REPO_ROOT / ".claude" / "skills" / "ai-fluency"
COLLECT = SKILL_DIR / "scripts" / "collect.py"


class SkillFilesTests(unittest.TestCase):
    def test_skill_files_exist(self):
        self.assertTrue((SKILL_DIR / "SKILL.md").exists())
        self.assertTrue(COLLECT.exists())

    def test_frontmatter_has_required_fields(self):
        text = (SKILL_DIR / "SKILL.md").read_text()
        match = re.match(r"^---\n(.*?)\n---", text, re.S)
        self.assertIsNotNone(match, "SKILL.md must start with a YAML frontmatter block")
        fm = match.group(1)
        self.assertRegex(fm, r"(?m)^name:\s*ai-fluency\s*$")
        self.assertRegex(fm, r"(?m)^description:\s*\S")
        # The collector the skill body invokes must actually exist.
        self.assertIn("collect.py", text)


class CollectorTests(unittest.TestCase):
    def test_collector_runs_from_foreign_cwd(self):
        # Runs without `pip install` and from a directory that isn't the repo,
        # proving the sys.path fallback works.
        with tempfile.TemporaryDirectory() as d:
            r = subprocess.run(
                [sys.executable, str(COLLECT), "--mock"],
                cwd=d, capture_output=True, text=True,
            )
            self.assertEqual(r.returncode, 0, r.stderr)
            payload = json.loads(r.stdout)
            self.assertIn("metrics", payload)
            self.assertIn("sample_prompts", payload)
            self.assertIn("archetype", payload["metrics"])


if __name__ == "__main__":
    unittest.main()
