"""End-to-end CLI tests — run the tool as a real user would (subprocess)."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def run_cli(*args):
    """Run `python -m claude_insight ...` from the repo root."""
    return subprocess.run(
        [sys.executable, "-m", "claude_insight", *args],
        cwd=str(REPO_ROOT), capture_output=True, text=True,
    )


class CliEndToEndTests(unittest.TestCase):
    def test_mock_heuristic_report(self):
        r = run_cli("--mock", "--no-ai")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("CLAUDE INSIGHT", r.stdout)
        self.assertIn("Builder Archetype", r.stdout)

    def test_json_export_is_clean_json(self):
        r = run_cli("--mock", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        # stdout must be pure JSON (status went to stderr).
        payload = json.loads(r.stdout)
        self.assertIn("metrics", payload)
        self.assertIn("sample_prompts", payload)
        self.assertGreater(len(payload["sample_prompts"]), 0)
        self.assertIn("archetype", payload["metrics"])
        # AI-only fields must not appear in the JSON export.
        self.assertNotIn("llm_summary", payload["metrics"])

    def test_json_status_goes_to_stderr(self):
        r = run_cli("--mock", "--json")
        self.assertIn("mock data", r.stderr.lower())
        self.assertNotIn("mock data", r.stdout.lower())

    def test_html_report_generated(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "report.html"
            r = run_cli("--mock", "--no-ai", "--report", str(out))
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertTrue(out.exists())
            html = out.read_text()
            self.assertIn("<!DOCTYPE html>", html)
            self.assertIn("Claude Insight", html)

    def test_real_test_fixtures(self):
        r = run_cli("--dir", str(REPO_ROOT / "tests" / "data"), "--no-ai")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("CLAUDE INSIGHT", r.stdout)

    def test_no_transcripts_exits_nonzero(self):
        with tempfile.TemporaryDirectory() as d:
            r = run_cli("--dir", d, "--no-ai")
            self.assertEqual(r.returncode, 1)
            self.assertIn("No Claude Code transcripts", r.stdout + r.stderr)

    def test_version(self):
        r = run_cli("--version")
        self.assertEqual(r.returncode, 0)
        self.assertIn("claude-insight", r.stdout)


if __name__ == "__main__":
    unittest.main()
