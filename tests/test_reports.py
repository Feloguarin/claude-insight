"""Tests for terminal and HTML report rendering."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from claude_insight.analyzer.metrics import AggregateMetrics
from claude_insight.reports.terminal import TerminalReport
from claude_insight.reports.html_report import HTMLReport


def metrics_with_ai():
    m = AggregateMetrics(total_sessions=2, total_prompts=4,
                         archetype="🏗️ Architect", efficiency_score=72)
    m.archetype_scores = {"🏗️ Architect": 60.0, "⚡ Sprinter": 40.0}
    m.tool_usage = {"Read": 3, "Edit": 1}
    m.llm_summary = "You design first."
    m.llm_archetype_reason = "Plans before building."
    m.llm_model = "gemma3:4b"
    m.growth_recommendations = ["Add file paths", "Try Grep", "Define interfaces"]
    return m


class TerminalReportTests(unittest.TestCase):
    def test_renders_ai_sections(self):
        out = TerminalReport(metrics_with_ai()).generate()
        self.assertIn("Profile Summary", out)
        self.assertIn("You design first.", out)
        self.assertIn("gemma3:4b", out)

    def test_heuristic_mode_has_no_ai_summary(self):
        m = AggregateMetrics(total_sessions=1, archetype="⚡ Sprinter")
        out = TerminalReport(m).generate()
        self.assertNotIn("Profile Summary", out)
        self.assertIn("All analysis performed locally", out)


class HtmlReportTests(unittest.TestCase):
    def test_renders_and_contains_sections(self):
        html = HTMLReport(metrics_with_ai()).generate()
        self.assertIn("<!DOCTYPE html>", html)
        self.assertIn("AI Profile Summary", html)
        self.assertIn("gemma3:4b", html)

    def test_escapes_injected_values(self):
        m = AggregateMetrics(total_sessions=1, archetype="🏗️ Architect")
        m.llm_summary = "<script>alert('x')</script>"
        html = HTMLReport(m).generate()
        self.assertNotIn("<script>alert('x')</script>", html)
        self.assertIn("&lt;script&gt;", html)


if __name__ == "__main__":
    unittest.main()
