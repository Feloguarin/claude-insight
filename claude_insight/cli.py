#!/usr/bin/env python3
"""Claude Insight CLI - Local AI coding session analyzer."""

import argparse
import sys
from pathlib import Path

from .parser.transcript import TranscriptParser
from .analyzer.metrics import MetricsAnalyzer
from .reports.terminal import TerminalReport
from .reports.html_report import HTMLReport


def analyze_command(args):
    """Analyze Claude Code transcripts."""
    path = Path(args.path)
    
    if not path.exists():
        print(f"❌ Path not found: {path}", file=sys.stderr)
        sys.exit(1)
    
    # Parse transcripts
    parser = TranscriptParser(str(path))
    sessions = parser.parse_all() if path.is_dir() else [parser.parse_file(path)]
    
    if not sessions:
        print("❌ No valid transcripts found.", file=sys.stderr)
        sys.exit(1)
    
    # Analyze metrics
    analyzer = MetricsAnalyzer()
    metrics = analyzer.analyze_all(sessions)
    
    # Generate report
    if args.format == "html":
        report = HTMLReport(metrics)
        output = report.generate()
    elif args.format == "json":
        import json
        output = json.dumps(metrics, indent=2, default=str)
    else:
        report = TerminalReport(metrics)
        output = report.generate()
    
    if args.output:
        Path(args.output).write_text(output)
        print(f"✅ Report saved to: {args.output}")
    else:
        print(output)


def version_command(args):
    """Show version."""
    from . import __version__
    print(f"Claude Insight v{__version__}")


def main():
    parser = argparse.ArgumentParser(
        prog="claude-insight",
        description="Local AI coding session analyzer",
    )
    parser.add_argument("--version", action="version", version="%(prog)s 0.1.0")
    
    subparsers = parser.add_subparsers(dest="command", help="Commands")
    
    # Analyze command
    analyze_parser = subparsers.add_parser("analyze", help="Analyze transcripts")
    analyze_parser.add_argument("path", help="Path to transcript file or directory")
    analyze_parser.add_argument("--format", choices=["terminal", "html", "json"], default="terminal")
    analyze_parser.add_argument("--output", "-o", help="Output file path")
    analyze_parser.set_defaults(func=analyze_command)
    
    # Report command (alias for analyze)
    report_parser = subparsers.add_parser("report", help="Generate report")
    report_parser.add_argument("path", help="Path to transcript file or directory")
    report_parser.add_argument("--format", choices=["terminal", "html", "json"], default="html")
    report_parser.add_argument("--output", "-o", help="Output file path")
    report_parser.set_defaults(func=analyze_command)
    
    # Version command
    version_parser = subparsers.add_parser("version", help="Show version")
    version_parser.set_defaults(func=version_command)
    
    args = parser.parse_args()
    
    if args.command:
        args.func(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
