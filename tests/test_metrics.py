"""Tests for the metrics analyzer, especially the Tier 1 correctness fixes."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from claude_insight.parser.transcript import Session, Message
from claude_insight.analyzer.metrics import MetricsAnalyzer, AggregateMetrics


def session_from_prompts(prompts, session_id="s", tool_calls=None):
    msgs = []
    for p in prompts:
        msgs.append(Message(role="user", content=p))
        msgs.append(Message(role="assistant", content="ok", tool_calls=tool_calls or []))
    return Session(session_id=session_id, messages=msgs)


class ArchetypeTests(unittest.TestCase):
    def setUp(self):
        self.analyzer = MetricsAnalyzer()

    def test_word_boundary_avoids_false_positives(self):
        # "address" must not match "add"; "building" must not match "build".
        s = session_from_prompts([
            "please update the address field in the building config",
        ])
        _, scores = self.analyzer.detect_archetype([s])
        # No archetype keyword genuinely appears as a whole word here, so the
        # Sprinter score (which "add"/"build" would have inflated) stays 0.
        self.assertEqual(scores.get("⚡ Sprinter", 0), 0)

    def test_detects_architect(self):
        s = session_from_prompts([
            "Let's design the architecture and compare patterns before implementing.",
            "I want to plan the interface and structure first.",
        ])
        archetype, _ = self.analyzer.detect_archetype([s])
        self.assertEqual(archetype, "🏗️ Architect")

    def test_detects_debugger(self):
        s = session_from_prompts([
            "There is a bug, why is this broken? Trace the error.",
            "It's still not working, fix the issue.",
        ])
        archetype, _ = self.analyzer.detect_archetype([s])
        self.assertEqual(archetype, "🐛 Debugger")


class ProductScoreTests(unittest.TestCase):
    def setUp(self):
        self.analyzer = MetricsAnalyzer()

    def test_product_terms_raise_score(self):
        with_product = session_from_prompts([
            "Ship this feature so the user gets more value.",
            "What does the customer experience here?",
        ])
        without = session_from_prompts([
            "Refactor the parser.",
            "Tweak the regex.",
        ])
        hi = self.analyzer.analyze_all([with_product]).product_score
        lo = self.analyzer.analyze_all([without]).product_score
        self.assertGreater(hi, lo)


class EfficiencyTests(unittest.TestCase):
    def setUp(self):
        self.analyzer = MetricsAnalyzer()

    def test_more_sessions_not_penalized(self):
        # The old bug subtracted points per session. Analyzing the same data
        # five times should not score worse than once.
        one = session_from_prompts(["design the architecture and plan"], "a")
        five = [session_from_prompts(["design the architecture and plan"], f"s{i}") for i in range(5)]
        score_one = self.analyzer.analyze_all([one]).efficiency_score
        score_five = self.analyzer.analyze_all(five).efficiency_score
        self.assertAlmostEqual(score_one, score_five, delta=0.5)

    def test_errors_lower_efficiency(self):
        clean = Session(session_id="c", messages=[
            Message(role="user", content="add the endpoint"),
            Message(role="assistant", content="done, all good"),
        ])
        erroring = Session(session_id="e", messages=[
            Message(role="user", content="add the endpoint"),
            Message(role="assistant", content="error: failed, cannot, unable, exception"),
        ])
        clean_score = self.analyzer.analyze_all([clean]).efficiency_score
        err_score = self.analyzer.analyze_all([erroring]).efficiency_score
        self.assertLess(err_score, clean_score)

    def test_error_count_is_aggregated(self):
        erroring = Session(session_id="e", messages=[
            Message(role="user", content="go"),
            Message(role="assistant", content="error occurred"),
        ])
        m = self.analyzer.analyze_all([erroring])
        self.assertEqual(m.total_errors, 1)


class AggregateTests(unittest.TestCase):
    def setUp(self):
        self.analyzer = MetricsAnalyzer()

    def test_empty_returns_defaults(self):
        m = self.analyzer.analyze_all([])
        self.assertIsInstance(m, AggregateMetrics)
        self.assertEqual(m.total_sessions, 0)

    def test_scores_within_range(self):
        s = session_from_prompts([
            "design the system, ship the feature for the user",
            "fix the bug and run the tests",
        ], tool_calls=[{"type": "Read"}, {"type": "Edit"}])
        m = self.analyzer.analyze_all([s])
        for score in (m.steering_score, m.execution_score, m.engineering_score,
                      m.product_score, m.planning_score, m.efficiency_score):
            self.assertGreaterEqual(score, 0)
            self.assertLessEqual(score, 100)

    def test_real_duration_flows_into_aggregate(self):
        s = Session(session_id="t", messages=[
            Message(role="user", content="hi", timestamp="2024-01-01T12:00:00Z"),
            Message(role="assistant", content="yo", timestamp="2024-01-01T13:00:00Z"),
        ])
        m = self.analyzer.analyze_all([s])
        self.assertAlmostEqual(m.total_coding_time_hours, 1.0, places=2)


if __name__ == "__main__":
    unittest.main()
