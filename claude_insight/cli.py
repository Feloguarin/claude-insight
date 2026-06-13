#!/usr/bin/env python3
"""
Claude Insight — Private AI Builder Profiler
Command-line interface.
"""

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from claude_insight import __version__
from claude_insight.parser.transcript import TranscriptParser
from claude_insight.analyzer.metrics import MetricsAnalyzer
from claude_insight.analyzer.llm import LocalLLMAnalyzer, DEFAULT_MODEL
from claude_insight.reports.terminal import TerminalReport
from claude_insight.reports.html_report import HTMLReport


def main():
    parser = argparse.ArgumentParser(
        prog="claude-insight",
        description="Claude Insight: Private AI Builder Analytics",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )

    parser.add_argument(
        "--version", action="version", version=f"claude-insight {__version__}"
    )

    parser.add_argument(
        "--dir", "-d",
        help="Directory containing Claude Code .jsonl transcripts (default: ~/.claude/projects)"
    )

    parser.add_argument(
        "--report", "-r",
        help="Generate an HTML report file (e.g., report.html)"
    )

    parser.add_argument(
        "--mock", action="store_true",
        help="Generate and use mock data for testing purposes"
    )

    parser.add_argument(
        "--json", action="store_true", dest="json_out",
        help="Output deterministic metrics + sample prompts as JSON, then exit "
             "(for the Claude Code skill or other consumers; skips AI enrichment)"
    )

    parser.add_argument(
        "--no-ai", action="store_true",
        help="Skip the local AI model and use heuristic analysis only"
    )

    parser.add_argument(
        "--model", default=None,
        help=f"Local Ollama model for AI analysis (default: {DEFAULT_MODEL})"
    )

    args = parser.parse_args()

    # 1. Initialize
    analyzer = MetricsAnalyzer()

    # 2. Handle Mock Data
    if args.mock:
        # Status to stderr so --json keeps stdout pure JSON.
        print("🧪 Using mock data for analysis...", file=sys.stderr)
        sessions = generate_mock_sessions()
    else:
        # 3. Parse Transcripts
        transcript_parser = TranscriptParser(args.dir)
        sessions = transcript_parser.parse_all()

        if not sessions:
            print("❌ No Claude Code transcripts found.")
            print("   Default paths searched: ~/.claude/projects, ~/.claude/sessions")
            print("\n   Try running with --mock to see how it looks, or specify a path with --dir")
            sys.exit(1)

    # 4. Analyze (deterministic metrics)
    if not args.json_out:
        print(f"🧐 Analyzing {len(sessions)} session(s)...")
    aggregate_metrics = analyzer.analyze_all(sessions)

    # JSON export: emit data for an external analyzer (e.g. the Claude Code
    # skill) and exit. No AI enrichment — the consumer does the qualitative work.
    if args.json_out:
        print(json.dumps(metrics_to_payload(aggregate_metrics, sessions), indent=2))
        return

    # 4b. Enrich with a local AI model (Ollama), if available
    if not args.no_ai:
        enrich_with_local_model(aggregate_metrics, sessions, args.model)

    # 5. Generate Terminal Report
    term_report = TerminalReport(aggregate_metrics)
    print(term_report.generate())

    # 6. Generate HTML Report
    if args.report:
        html_gen = HTMLReport(aggregate_metrics)
        report_path = Path(args.report).absolute()
        with open(report_path, "w", encoding="utf-8") as f:
            f.write(html_gen.generate())
        print(f"✨ HTML Report generated: {report_path}")


def metrics_to_payload(metrics, sessions, max_prompts=80, max_chars=12000):
    """Build a JSON-serializable payload of metrics + a sample of prompts.

    Consumed by the Claude Code skill (and any other external analyzer) so the
    qualitative analysis can be done with full context.
    """
    sample_prompts = []
    total = 0
    for session in sessions:
        for msg in session.user_messages:
            text = (msg.content or "").strip()
            if not text:
                continue
            if total + len(text) > max_chars or len(sample_prompts) >= max_prompts:
                break
            sample_prompts.append(text)
            total += len(text)

    # Drop the LLM-only fields — they're not populated in JSON mode.
    metrics_dict = asdict(metrics)
    for key in ("llm_summary", "llm_archetype_reason", "llm_model"):
        metrics_dict.pop(key, None)

    return {"metrics": metrics_dict, "sample_prompts": sample_prompts}


def enrich_with_local_model(metrics, sessions, model=None):
    """Run the local LLM analysis and merge its output into the metrics.

    Falls back silently to heuristic results if Ollama isn't running or the
    model isn't installed — the tool always produces a report.
    """
    llm = LocalLLMAnalyzer(model=model)

    if not llm.is_available():
        print(f"   💡 Local model ({llm.model}) not found — using heuristic analysis.")
        print(f"      Install Ollama and run: ollama pull {llm.model}")
        return

    print(f"🤖 Analyzing with local model: {llm.model} (this stays on your machine)...")

    sample_prompts = []
    for session in sessions:
        sample_prompts.extend(m.content for m in session.user_messages if m.content)

    insights = llm.analyze(metrics, sample_prompts)
    if not insights:
        print("   ⚠️  Local model analysis failed — using heuristic results.")
        return

    # Merge: prefer the model's qualitative judgement, keep numeric metrics.
    if insights.archetype:
        metrics.archetype = insights.archetype
    if insights.recommendations:
        metrics.growth_recommendations = insights.recommendations
    metrics.llm_summary = insights.summary
    metrics.llm_archetype_reason = insights.archetype_reason
    metrics.llm_model = insights.model


def generate_mock_sessions():
    """Generates mock session data for testing the analyzer logic."""
    from claude_insight.parser.transcript import Session, Message

    # Session 1: The Architect
    s1 = Session(session_id="arch-001")
    s1.messages = [
        Message(role="user", content="I want to design a new architecture for a microservice. Let's compare patterns."),
        Message(role="assistant", content="Thinking...", tool_calls=[{"type": "Read", "input": {"path": "main.py"}}]),
        Message(role="user", content="Before implementing, let's create a detailed plan for the interface."),
    ]

    # Session 2: The Sprinter
    s2 = Session(session_id="sprint-001")
    s2.messages = [
        Message(role="user", content="Quick, add a simple endpoint to the API."),
        Message(role="assistant", content="Sure.", tool_calls=[{"type": "Write", "input": {"path": "api.py"}}]),
        Message(role="user", content="Now implement the user auth quickly."),
    ]

    return [s1, s2]


if __name__ == "__main__":
    main()
