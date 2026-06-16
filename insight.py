#!/usr/bin/env python3
"""
Claude Insight v2 — one-command, zero-install AI-fluency analyzer.

    python3 insight.py

Reads your local Claude Code transcripts (~/.claude/projects/**/*.jsonl),
estimates how skillfully you drive an AI coding agent, and writes a single
self-contained HTML report (./ai_fluency_report.html) that opens in your browser.

Design principles (see README "Methodology"):
  * It measures SKILL, not activity. Every score input is a per-prompt or
    per-opportunity RATE pushed through a saturating curve, so using the agent
    MORE can never raise your score — only using it BETTER can.
  * It only looks at YOUR real typed prompts and Claude's real tool actions.
    Tool-results, subagent turns, slash-command stubs, injected system text and
    pasted walls of text are filtered out before anything is scored.
  * Every number is auditable: baselines are recomputed from your corpus at
    runtime, formulas are documented, and thin signals are flagged "low data"
    and pulled toward a neutral 50 instead of faking confidence.

Pure Python standard library — no pip, no Ollama, no API key. One command runs the
whole pass: de-contaminate and scrub your transcripts, score them, and (as
`/ai-fluency` in Claude Code) write a Sonnet+Opus skill map grounded in the AI
Fluency framework on top. The only thing it writes is
the HTML report and a local copy of your transcripts in an archive
(~/.claude/insight-archive) so history survives Claude Code's 30-day cleanup —
pass --no-archive to skip that and read your transcripts without copying them.
"""

import argparse
import glob
import html
import json
import math
import os
import re
import shutil
import statistics
import sys
import tempfile
import webbrowser
from collections import Counter, defaultdict
from datetime import datetime, timedelta

# --------------------------------------------------------------------------- #
# Constants & tunables (documented; shown in the report's methodology appendix)
# --------------------------------------------------------------------------- #

DEFAULT_DIRS = ["~/.claude/projects", "~/.claude/sessions"]

# Claude Code deletes transcripts older than its `cleanupPeriodDays` setting (default 30),
# so by default only ~30 days of history is ever on disk. We mirror each run's transcripts
# into this persistent archive so history accumulates indefinitely and survives the cleanup.
# Point it at a synced folder (Dropbox/iCloud) to keep it across machines.
DEFAULT_ARCHIVE_DIR = "~/.claude/insight-archive"

GAP_CAP_SECONDS = 300          # idle gaps longer than this are NOT counted as active time
MAX_HUMAN_PROMPT_CHARS = 6000  # anything longer is treated as a paste/injection, not a typed prompt
PROVISIONAL_MIN_PROMPTS = 30   # below this the headline score is shown as a hedged range

EDIT_TOOLS = {"edit", "write", "multiedit", "notebookedit"}
READ_TOOLS = {"read", "grep", "glob"}

# Text that marks a "user"-role record as injected/system rather than typed by the human.
INJECTION_MARKERS = (
    "<task-notification>", "<command-name>", "<command-message>", "<command-args>",
    "<local-command-caveat>", "<local-command-stdout>", "<system-reminder>",
    "<bash-input>", "<bash-stdout>", "caveat: the messages below",
    "[request interrupted", "base directory for this skill", "<user-prompt-submit-hook>",
    "<user-memory-input>", "this session is being continued",
)

# Subagent system prompts get stored as plain user-role text with no other marker.
# They almost always open with "You are <role>…". This catches the back-door inflation.
_INJECTED_HEAD = re.compile(
    r"^\s*(you are\b|<[a-z][\w-]*>|base directory for this skill)", re.I
)

# Broad, project-extensible verification matcher (matched against real Bash commands).
VERIFY_RE = re.compile(
    r"\b("
    r"pytest|unittest|jest|vitest|mocha|go test|cargo (test|build|check)|"
    r"npm (run )?(test|build|lint)|yarn (test|build|lint)|pnpm (test|build|lint)|"
    r"ruff|eslint|flake8|mypy|tsc\b|make (test|lint|build|check)|playwright|"
    r"python\d? -m \w|\.venv/bin/python|lsof -ti|curl .*(localhost|127\.0\.0\.1)|"
    r"docker compose|docker-compose|pre-commit"
    r")",
    re.I,
)
# Clean-teardown of a live system (small bonus, folded into Verification).
TEARDOWN_RE = re.compile(r"(lsof -ti.*kill|pkill|kill -9|docker compose down|docker-compose down)", re.I)

# Direction (prompt-quality) cues.
ARTIFACT_RE = re.compile(
    r"([\w./\-]+\.(py|js|ts|tsx|jsx|html|css|md|json|sh|ya?ml|toml|rs|go|java|cpp|c|rb|sql))"
    r"|((?:/[\w.\-]+){2,})"        # multi-segment paths (not bare /word or </tag>)
    r"|(`[^`]+`)"                  # inline code / quoted token
    r"|(\b\w+\(\))",               # function() reference
    re.I,
)
CONSTRAINT_CUE = re.compile(
    r"\b(only|must|should|shouldn't|don't|do not|never|always|keep|ensure|instead of|"
    r"at most|at least|exactly|without|except|make sure|no more than|leave .* as is)\b", re.I
)
INTENT_CUE = re.compile(
    r"\b(so that|because|the goal is|in order to|for the demo|for my|for the|so i can|so we can|"
    r"so it|i need|i want .* so)\b", re.I
)
ACTION_VERB = re.compile(
    r"\b(add|create|build|make|implement|write|fix|change|update|refactor|remove|delete|run|"
    r"generate|set up|setup|install|deploy|edit|rename|move|clean|stitch|speed up|merge|split)\b", re.I
)

# Iteration cues.
CORRECTION_CUE = re.compile(
    r"\b(no|nope|wrong|not quite|that's not|thats not|actually|instead|revert|undo|redo|try again|"
    r"too (aggressive|agressive|much|many|slow|fast|big|small)|still (broken|failing|wrong|not)|"
    r"doesn't work|does not work|not working|unteligible|unteliggeble)\b", re.I
)
PRAISE_CUE = re.compile(r"\b(great|perfect|love it|nice|awesome|excellent|beautiful|exactly)\b", re.I)
CORRECTION_RATE_CEILING = 0.35   # a "high" correction rate; lower is better

# Delegation / planning tool signals.
DELEGATION_TOOLS = {"agent", "task", "workflow", "exitplanmode", "enterplanmode"}

# Dimension weights (sum to 1.0).
WEIGHTS = {
    "Direction": 0.24,
    "Verification": 0.22,
    "Context": 0.22,
    "Iteration": 0.18,
    "Toolcraft": 0.14,
}
# Opportunity-count targets for per-dimension confidence shrinkage.
TARGET_N = {"Direction": 60, "Verification": 15, "Context": 25, "Iteration": 12, "Toolcraft": 40}

# User-facing labels. "Direction" is shown as "Briefing" so it never collides with the
# "Director" archetype (the dimension measures how well you brief; the archetype, that
# you delegate — different things).
DISPLAY_NAMES = {"Direction": "Briefing", "Verification": "Verification",
                 "Context": "Context-setting", "Iteration": "Iteration", "Toolcraft": "Toolcraft"}

def disp(name):
    return DISPLAY_NAMES.get(name, name)

# Teacher content for each skill (kind, plain-English, with before/after examples and a
# weekly practice). Used to make the report explain what to improve and exactly how.
SKILL_TEACH = {
    "Direction": {
        "what_it_is": "Telling the agent what you want and giving it something to aim at: a goal plus a file, a constraint, or a way to know it worked.",
        "why_it_matters": "When your goal and your limits are clear up front, the agent gets it right the first time instead of guessing and pulling you into rounds of fixes.",
        "how_to_improve": "Before you hit enter, add one anchor to your goal: the file to touch, a rule it must not break, or a 'done when…' line. One line is plenty.",
        "examples": [
            {"before": "fix the login bug", "after": "Users stay logged out after a correct password on Safari. The check lives in src/auth/session.ts. Fix it so a valid login sets the session cookie, and keep the current tests green."},
            {"before": "add caching to the API", "after": "Cache GET /products responses in api/products.py for 60s to ease DB load on repeat reads. Don't cache authed requests, and add a test that a second call within 60s skips the DB."},
        ],
        "practice": "Before sending a prompt, add one anchor to your goal: a file path, a constraint, or a 'done when…' line.",
        "good_looks_like": "Every request says what you want plus where to work or how success is judged, so the agent acts instead of guessing.",
    },
    "Verification": {
        "what_it_is": "Having the agent prove its own work — run the tests, build, lint, or launch the app — before it tells you it's done.",
        "why_it_matters": "Code that looks right but was never run is where most AI bugs hide; checking it turns “probably works” into “I watched it work.”",
        "how_to_improve": "In the same prompt that asks for the change, name the exact command that proves it (a test, build, lint, or curl) and tell the agent to run it and show you the output before stopping.",
        "examples": [
            {"before": "Fix the off-by-one in the pagination helper.", "after": "Fix the off-by-one in the pagination helper, then run `pytest tests/test_pagination.py -x` and paste the output. Don't call it fixed until that test passes."},
            {"before": "Add a /health endpoint to the FastAPI server.", "after": "Add a /health endpoint to the FastAPI server. Start it on port 8000, curl `localhost:8000/health`, and show me the response. Run `ruff check` too and confirm it's clean before you finish."},
        ],
        "practice": "Before you accept any change, ask: “How did you verify this? Run it and show me the output.”",
        "good_looks_like": "Every change ends with proof — a passing test, a green build, a real response — pasted back to you, not just a claim.",
    },
    "Context": {
        "what_it_is": "Pointing the agent at the real code — a file, a function, a line area — and having it read that before it changes anything.",
        "why_it_matters": "When the agent sees the actual current code first, its edits fit what's really there instead of a guess, so they apply cleanly the first time.",
        "how_to_improve": "Before any edit, name the exact file (and the function or area if you can) and tell the agent to read it first. Let it look before it leaps.",
        "examples": [
            {"before": "Add retry logic to the API client.", "after": "Read src/api/client.ts first, then add retry-with-backoff to the request() method. Show me the change before you apply it."},
            {"before": "Fix the timezone bug in the date formatter.", "after": "Open src/utils/date.ts and find formatDate(). Read how it handles timezones now, then fix the off-by-one so UTC inputs render in the user's local zone."},
        ],
        "practice": "Start your next edit request with “Read <file> first, then…” so the agent grounds itself before touching anything.",
        "good_looks_like": "Every edit lands on code the agent just read, so diffs apply cleanly with nothing broken around them.",
    },
    "Iteration": {
        "what_it_is": "When the agent goes the wrong way, steering it back with a precise correction — naming what broke and the rule to follow — instead of just “no” or “try again.”",
        "why_it_matters": "A precise correction lands the fix in one round; a vague “no” makes the agent guess again, and you burn turns while the code drifts further off.",
        "how_to_improve": "When a result is wrong, say three things in one message: the symptom you saw, the rule it broke, and what to do instead. Then let it run.",
        "examples": [
            {"before": "no that's not right, try again", "after": "The retry loop catches the exception but never re-raises after the last attempt, so failures look like successes. Re-raise the original error once retries run out, and keep the existing backoff."},
            {"before": "this is wrong, fix the test", "after": "The test passes because you mocked the function under test instead of the network call. Don't mock get_user — mock requests.get inside it, and assert it was called with the real URL."},
        ],
        "practice": "Before sending a correction, check it names both the symptom and the rule. If it only says “no,” add the missing half.",
        "good_looks_like": "One sharp correction — symptom, rule, and the fix — and the agent lands it on the next try.",
    },
    "Toolcraft": {
        "what_it_is": "Letting the agent use the right tool for each step — searching the code, running commands, starting the app, working in the background — instead of forcing everything through chat.",
        "why_it_matters": "The agent works faster and more reliably when it searches and runs things for real, rather than reasoning about the code from memory.",
        "how_to_improve": "Tell the agent which action to take first — search the codebase, run the suite, start the server — so it gathers facts and checks its work with the tool built for each step.",
        "examples": [
            {"before": "How does login work in this app?", "after": "Search the codebase for the login flow (grep for auth, session, login), read the files you find, then explain how a request goes from form submit to a logged-in session."},
            {"before": "Add a retry to the API client, and make sure the tests still pass.", "after": "Add retry-with-backoff to the API client. Then run the suite in the background; if anything fails, read the failure, fix it, and report back when it's green."},
        ],
        "practice": "Add one line to your next task telling the agent which action to take first: “search for…”, “run the tests”, or “start the server and check.”",
        "good_looks_like": "You hand off a whole job and the agent searches, edits, runs, and verifies on its own — each step using the tool made for it.",
    },
}

BANDS = [
    ("Operator", 0, 39, "You use the agent as fast hands. Prompts are short and underspecified, "
     "edits often happen without reading the file first, and changes are rarely verified. The "
     "fastest gains live right here: state a goal plus one constraint, and let the agent read "
     "before it edits."),
    ("Developing", 40, 54, "Real back-and-forth is emerging and one or two habits are solid. Some "
     "prompts carry a file path or a constraint; verification happens occasionally. The gap to the "
     "next level is consistency — doing the right thing by default, not just sometimes."),
    ("Proficient", 55, 69, "You drive the agent deliberately. Most prompts are specific, edits "
     "usually follow a read of the same file, and you verify more often than not. Solid, reliable "
     "AI-assisted engineering. Remaining gains are about altitude (saying why) and orchestration."),
    ("Advanced", 70, 84, "You orchestrate rather than operate. Prompts encode goals, constraints "
     "and acceptance criteria; reading precedes editing as a habit; verification is near-automatic; "
     "you use planning and delegation fluently. You brief the agent like a senior teammate."),
    ("Expert", 85, 100, "You treat the agent as a managed engineering system: consistently "
     "high-context prompts with explicit success criteria, disciplined read→edit→verify loops, "
     "deliberate delegation, and almost no wasted correction cycles."),
]

# Archetype axes and prototypes.
# The archetype describes YOUR DRIVING STYLE, so it is built only from signals you
# control and DISCOUNTS the habits Claude does on its own. Verification and Context
# (read-before-edit, running tests) are largely the agent's defaults, so they carry
# low "agency" weight; how you brief (Direction), correct (Iteration), reach for tools
# (Toolcraft) and hand off work (Delegation) carry full weight.
ARCHETYPE_AXES = ["Direction", "Verification", "Context", "Iteration", "Toolcraft", "Delegation"]
AGENCY = {"Direction": 1.0, "Verification": 0.35, "Context": 0.15,
          "Iteration": 1.0, "Toolcraft": 0.8, "Delegation": 1.0}

# Prototype vectors over ARCHETYPE_AXES (0-100). Delegation is the axis that separates
# a hands-off delegator from a hands-on builder. These are the five explicit, recognizable
# builder archetypes; the classifier picks the nearest one from your AGENCY-WEIGHTED vector.
PROTOTYPES = {
    "Autonomous Agent": {"emoji": "🤖", "vec": [58, 65, 62, 62, 85, 96],
        "blurb": "You delegate whole, end-to-end jobs and trust the agent to run them — you set the outcome and let Claude pick the steps."},
    "Architect":        {"emoji": "🏗️", "vec": [80, 66, 88, 65, 60, 48],
        "blurb": "You plan and explore before you build — you read and design first, so changes land on a clear structure."},
    "Debugger":         {"emoji": "🐛", "vec": [62, 88, 82, 85, 60, 28],
        "blurb": "You hunt problems methodically — read to diagnose, change, verify, and repeat until it's truly fixed."},
    "Collaborator":     {"emoji": "🤝", "vec": [66, 62, 66, 80, 55, 38],
        "blurb": "You work with the agent like a teammate — ask for options, give feedback, and steer toward alignment."},
    "Sprinter":         {"emoji": "⚡", "vec": [45, 38, 52, 46, 62, 30],
        "blurb": "You move fast and direct — terse prompts, quick turns, low ceremony. Great velocity; briefing and verification are the growth edges."},
}
ARCHETYPE_MARGIN = 0.06   # cosine-similarity margin below which we emit a blended label


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #

def _parse_ts(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def _text_of(content):
    """Concatenate the text blocks of a message content (str or list)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(
            b.get("text", "") for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        )
    return ""


def _is_tool_result(content):
    return isinstance(content, list) and any(
        isinstance(b, dict) and b.get("type") == "tool_result" for b in content
    )


def _looks_injected(text):
    head = text[:200].lstrip()
    if len(text) > MAX_HUMAN_PROMPT_CHARS:
        return True
    if _INJECTED_HEAD.match(head):
        return True
    low = text.lower()
    return any(m in low for m in INJECTION_MARKERS)


def _denamespace_tool(name):
    """mcp__<hash>__slack_read_thread -> slack_read_thread; keep core names as-is."""
    if name.startswith("mcp__"):
        parts = name.split("__")
        return parts[-1] if parts else name
    return name


# Redact machine-identifying home paths from free text before it is shown in the report or
# written to the evidence bundle. Applied only at PRESENTATION, never to the scored corpus,
# so scores stay byte-identical.
_HOME_PATH_RE = re.compile(r"(?:/Users/|/home/)[^/\s]+")
_WIN_HOME_RE = re.compile(r"[A-Za-z]:\\Users\\[^\\\s]+")


def _scrub_paths(text):
    """/Users/<name>/x -> ~/x ; bare /Users/<name> -> ~ ; same for /home/<name> and Windows."""
    if not isinstance(text, str):
        return text
    text = _HOME_PATH_RE.sub("~", text)
    text = _WIN_HOME_RE.sub("~", text)
    return text


class Corpus:
    """Everything we measured from the transcripts, cleanly separated from scoring."""

    def __init__(self):
        self.files = 0
        self.projects = set()
        self.total_bytes = 0
        self.user_records = 0
        self.filtered = Counter()       # why user records were not counted as prompts
        self.real_prompts = []          # list of dicts: text, project, session, idx
        self.tool_usage = Counter()     # de-namespaced tool name -> count
        self.total_tool_calls = 0
        self.delegation_events = 0
        self.first_ts = None
        self.last_ts = None
        self.active_seconds = 0.0
        # Per-session ordered timelines of {"kind": "prompt"|"tool", ...}
        self.sessions = {}              # session_id -> {"project","timeline":[...]}


# Agent-to-agent transcripts (Claude Code subagents, Workflow runs) live under a
# ".../subagents/..." path. They are NOT the user's own prompts — counting them would
# contaminate the assessment and inflate counts every time a workflow is run — so they
# are excluded from discovery (an explicitly named single file is still honored).
_SUBAGENT_RE = re.compile(r"[/\\]subagents[/\\]")

# Every record written by --demo carries this top-level key (real Claude Code transcripts
# never set it). The real analysis path refuses any file that carries it, so the fictional
# sample can never be injected into or skew a user's own assessment — even if a demo file is
# copied by hand into ~/.claude/projects or the archive. Only render_demo() opts back in.
_DEMO_MARK = "isDemoData"


def _is_demo_transcript(path):
    """True if `path` is fictional --demo data (first record carries the demo mark)."""
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    return bool(json.loads(line).get(_DEMO_MARK))
                except json.JSONDecodeError:
                    return False
    except OSError:
        return False
    return False


def _filter_transcripts(paths, allow_demo=False):
    out = []
    for p in paths:
        if _SUBAGENT_RE.search(p):
            continue
        if not allow_demo and _is_demo_transcript(p):
            continue
        out.append(p)
    return out


def discover_files(explicit, allow_demo=False):
    if explicit:
        p = os.path.expanduser(explicit)
        if os.path.isfile(p) and p.endswith(".jsonl"):
            if not allow_demo and _is_demo_transcript(p):
                return []
            return [p]
        if os.path.isdir(p):
            return _filter_transcripts(sorted(glob.glob(os.path.join(p, "**", "*.jsonl"), recursive=True)), allow_demo)
        return []
    env = os.environ.get("CLAUDE_PROJECTS_DIR")
    roots = [env] if env else DEFAULT_DIRS
    files = []
    for r in roots:
        rp = os.path.expanduser(r)
        if os.path.isdir(rp):
            files.extend(glob.glob(os.path.join(rp, "**", "*.jsonl"), recursive=True))
    return _filter_transcripts(sorted(set(files)), allow_demo)


def _dedupe_sessions(files):
    """When the same session shows up in more than one root (the live ~/.claude/projects dir
    AND the persistent archive — possibly under a since-renamed project folder, a different-case
    path, or a synced copy from another machine), keep a single copy of it: the largest one,
    since transcripts only ever grow, so the biggest file is the most complete. Claude Code
    session filenames are globally-unique IDs, so the filename alone identifies the session —
    keying on it (not the parent folder) is what makes the dedupe robust to all of the above."""
    best = {}
    for path in files:
        key = os.path.basename(path)
        try:
            size = os.path.getsize(path)
        except OSError:
            size = -1
        cur = best.get(key)
        if cur is None or size > cur[0]:
            best[key] = (size, path)
    return sorted(p for _, p in best.values())


def archive_transcripts(live_files, archive_dir):
    """Copy live transcripts into a persistent archive so they survive Claude Code's
    `cleanupPeriodDays` deletion. Each file is mirrored to
    <archive>/<project folder>/<session>.jsonl. We copy only when the archived copy is
    missing or strictly smaller than the live one (transcripts only grow, so a >= archive copy
    is the more complete one and must never be overwritten with a smaller/equal one). We write
    via a temp file + atomic replace, re-checking the archive size just before the swap so a
    concurrent run can't clobber a larger copy, and always clean up the temp file.
    Returns (n_new, n_updated); a stderr note is printed if any file could not be archived."""
    arch_root = os.path.expanduser(archive_dir)
    new = updated = failed = 0
    for path in live_files:
        project = os.path.basename(os.path.dirname(path)) or "default"
        dest_dir = os.path.join(arch_root, project)
        dest = os.path.join(dest_dir, os.path.basename(path))
        try:
            live_size = os.path.getsize(path)
        except OSError:
            continue
        arch_size = os.path.getsize(dest) if os.path.exists(dest) else -1
        if arch_size >= live_size:
            continue  # already archived an equal-or-more-complete copy
        tmp = dest + ".tmp"
        try:
            os.makedirs(dest_dir, exist_ok=True)
            shutil.copyfile(path, tmp)
            # Another run may have grown the archive while we were copying — don't shrink it.
            current = os.path.getsize(dest) if os.path.exists(dest) else -1
            if current >= live_size:
                continue
            os.replace(tmp, dest)  # atomic; never leaves a half-written archive copy
        except OSError:
            failed += 1
            continue
        finally:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass
        if arch_size < 0:
            new += 1
        else:
            updated += 1
    if failed:
        print(f"  Note: {failed} transcript(s) could not be archived to {archive_dir} "
              f"(check permissions / disk space). They were still analyzed from disk.",
              file=sys.stderr)
    return new, updated


def parse(files):
    c = Corpus()
    c.files = len(files)
    for path in files:
        project = os.path.basename(os.path.dirname(path)) or "default"
        c.projects.add(project)
        try:
            c.total_bytes += os.path.getsize(path)
        except OSError:
            pass
        session_id = os.path.splitext(os.path.basename(path))[0]
        timeline = []
        ts_in_file = []
        prompt_idx = 0
        try:
            fh = open(path, encoding="utf-8")
        except OSError:
            continue
        with fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    e = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ts = _parse_ts(e.get("timestamp"))
                if ts:
                    ts_in_file.append(ts)
                    c.first_ts = ts if c.first_ts is None or ts < c.first_ts else c.first_ts
                    c.last_ts = ts if c.last_ts is None or ts > c.last_ts else c.last_ts
                msg = e.get("message") if isinstance(e.get("message"), dict) else {}
                role = e.get("role") or msg.get("role") or e.get("type")
                content = msg.get("content", e.get("content"))

                if role == "assistant":
                    if isinstance(content, list):
                        for b in content:
                            if isinstance(b, dict) and b.get("type") == "tool_use":
                                raw = b.get("name", "unknown")
                                name = _denamespace_tool(raw)
                                c.tool_usage[name] += 1
                                c.total_tool_calls += 1
                                inp = b.get("input", {}) if isinstance(b.get("input"), dict) else {}
                                if name.lower() in DELEGATION_TOOLS:
                                    c.delegation_events += 1
                                if name.lower() == "bash" and inp.get("run_in_background"):
                                    c.delegation_events += 1
                                fpath = inp.get("file_path") or inp.get("path") or inp.get("notebook_path")
                                cmd = inp.get("command") if name.lower() == "bash" else None
                                timeline.append({
                                    "kind": "tool", "name": name.lower(),
                                    "file": fpath, "cmd": cmd,
                                })
                    continue

                if role != "user":
                    continue
                c.user_records += 1
                if _is_tool_result(content):
                    c.filtered["tool results"] += 1
                    continue
                if e.get("isSidechain") is True:
                    c.filtered["subagent turns"] += 1
                    continue
                if e.get("isMeta") is True:
                    c.filtered["meta-injected"] += 1
                    continue
                text = _text_of(content).strip()
                if not text:
                    c.filtered["empty"] += 1
                    continue
                if _looks_injected(text):
                    c.filtered["injected / pasted"] += 1
                    continue
                # A genuine, human-typed prompt.
                prompt_idx += 1
                rec = {"text": text, "project": project, "session": session_id, "idx": prompt_idx}
                c.real_prompts.append(rec)
                timeline.append({"kind": "prompt", "text": text, "rec": rec})

        if len(ts_in_file) >= 2:
            ts_in_file.sort()
            c.active_seconds += sum(
                min((ts_in_file[i + 1] - ts_in_file[i]).total_seconds(), GAP_CAP_SECONDS)
                for i in range(len(ts_in_file) - 1)
            )
        if timeline:
            c.sessions[session_id] = {"project": project, "timeline": timeline}
    return c


# --------------------------------------------------------------------------- #
# Scoring helpers
# --------------------------------------------------------------------------- #

def squash(x, target):
    """Saturating curve: hitting `target` maxes the signal; exceeding adds nothing."""
    if target <= 0:
        return 0.0
    return max(0.0, min(1.0, x / target))


def clamp(x, lo, hi):
    return max(lo, min(hi, x))


def _is_action_prompt(text):
    return bool(ACTION_VERB.search(text))


# --------------------------------------------------------------------------- #
# The five dimensions — each returns (score_0_100, detail_dict, evidence_list)
# --------------------------------------------------------------------------- #

def score_direction(corpus):
    prompts = corpus.real_prompts
    n = len(prompts)
    if n == 0:
        return 0.0, {"n": 0}, []
    constraint = artifact = intent = 0
    weak_examples = []
    for p in prompts:
        t = p["text"]
        has_artifact = bool(ARTIFACT_RE.search(t))
        has_constraint = bool(CONSTRAINT_CUE.search(t) and ACTION_VERB.search(t))
        has_intent = bool(INTENT_CUE.search(t))
        artifact += 1 if has_artifact else 0
        constraint += 1 if has_constraint else 0
        intent += 1 if has_intent else 0
        if _is_action_prompt(t) and not (has_artifact or has_constraint or has_intent) and len(t) < 120:
            weak_examples.append(p)
    constraint_rate = constraint / n
    artifact_rate = artifact / n
    intent_rate = intent / n
    # front-loading: penalize rules first revealed via a high-info correction
    corr = _find_corrections(corpus)
    new_rule_corrections = sum(1 for x in corr if x["high_info"])
    action_prompts = max(1, sum(1 for p in prompts if _is_action_prompt(p["text"])))
    front_loading = 1 - clamp(new_rule_corrections / action_prompts, 0, 1)
    score = 100 * (
        0.30 * squash(constraint_rate, 0.45)
        + 0.20 * squash(artifact_rate, 0.45)
        + 0.25 * squash(intent_rate, 0.30)
        + 0.25 * front_loading
    )
    detail = {
        "n": n, "constraint_rate": constraint_rate, "artifact_rate": artifact_rate,
        "intent_rate": intent_rate, "front_loading": front_loading,
    }
    return score, detail, weak_examples[:6]


def _iter_sessions(corpus):
    for sid, s in corpus.sessions.items():
        yield sid, s["project"], s["timeline"]


def _find_corrections(corpus):
    """Correction turns: short rejections that follow an assistant action, praise-guarded."""
    out = []
    for sid, project, timeline in _iter_sessions(corpus):
        saw_tool = False
        for ev in timeline:
            if ev["kind"] == "tool":
                saw_tool = True
                continue
            t = ev["text"]
            head = t[:160]
            if CORRECTION_CUE.search(head) and not PRAISE_CUE.search(head) and saw_tool:
                high_info = bool(
                    re.search(r"\d", t) or ARTIFACT_RE.search(t) or len(t.split()) >= 8
                    or INTENT_CUE.search(t)
                )
                out.append({"session": sid, "project": project, "text": t, "high_info": high_info})
            saw_tool = False  # reset: correction must directly follow an action turn
    return out


def score_iteration(corpus):
    prompts = corpus.real_prompts
    n = len(prompts)
    corr = _find_corrections(corpus)
    k = len(corr)
    if n == 0:
        return 50.0, {"n": 0, "corrections": 0}, []
    rate = k / n
    specificity = (sum(1 for x in corr if x["high_info"]) / k) if k else 1.0
    score = 100 * (0.6 * (1 - clamp(rate / CORRECTION_RATE_CEILING, 0, 1)) + 0.4 * specificity)
    low_info = [x for x in corr if not x["high_info"]]
    # Confidence is keyed on prompt count n (the opportunity count), NOT correction count k:
    # a user with many clean prompts and zero corrections has STRONG evidence of good iteration,
    # so it must not be shrunk toward 50 as if it were "no data".
    detail = {"n": n, "corrections": k, "correction_rate": rate, "specificity": specificity}
    return score, detail, low_info[:4]


def score_context(corpus):
    total_edits = 0
    grounded = 0
    blind_examples = []
    for sid, project, timeline in _iter_sessions(corpus):
        read_paths = set()
        edited_paths = set()
        written_paths = set()   # files the agent authored this session (grounded to edit)
        for ev in timeline:
            if ev["kind"] != "tool":
                continue
            name, fpath = ev["name"], ev.get("file")
            if name in READ_TOOLS and fpath:
                read_paths.add(fpath)
            elif name in EDIT_TOOLS:
                total_edits += 1
                if not fpath:
                    grounded += 1  # can't attribute; don't penalize
                    continue
                is_new_write = (name == "write" and fpath not in read_paths and fpath not in edited_paths)
                # grounded if it was read, OR authored earlier this session, OR is being created now
                if fpath in read_paths or fpath in written_paths or is_new_write:
                    grounded += 1
                else:
                    blind_examples.append({"session": sid, "project": project, "file": fpath})
                if name == "write":
                    written_paths.add(fpath)
                edited_paths.add(fpath)
    if total_edits == 0:
        return 50.0, {"n": 0, "grounded": 0, "total_edits": 0, "rate": None}, []
    rate = grounded / total_edits
    score = 100 * squash(rate, 0.85)
    return score, {"n": total_edits, "grounded": grounded, "total_edits": total_edits, "rate": rate}, blind_examples[:4]


def score_verification(corpus):
    episodes = 0
    verified = 0
    teardown_bonus = 0
    unverified_examples = []
    for sid, project, timeline in _iter_sessions(corpus):
        open_ep = False
        ep_files = []
        for ev in timeline:
            if ev["kind"] == "prompt":
                # a "run it / does it work / confirm" prompt verifies an open episode
                if open_ep and re.search(r"\b(run it|does it work|confirm|check (it|that)|verify|did it work)\b",
                                         ev["text"], re.I):
                    verified += 1
                    open_ep = False
                continue
            name = ev["name"]
            cmd = ev.get("cmd") or ""
            if name in EDIT_TOOLS:
                if not open_ep:
                    open_ep = True
                    episodes += 1
                    ep_files = []
                if ev.get("file"):
                    ep_files.append(os.path.basename(ev["file"]))
            elif name == "bash":
                if TEARDOWN_RE.search(cmd):
                    teardown_bonus = 5
                if open_ep and VERIFY_RE.search(cmd):
                    verified += 1
                    open_ep = False
            elif name in READ_TOOLS and open_ep and ev.get("file") and os.path.basename(ev["file"]) in ep_files:
                # re-reading the just-edited file is a (weak) check
                verified += 1
                open_ep = False
        if open_ep:
            unverified_examples.append({"session": sid, "project": project,
                                        "files": ", ".join(sorted(set(ep_files))[:3]) or "files"})
    if episodes == 0:
        return 50.0, {"n": 0, "episodes": 0, "verified": 0, "rate": None}, []
    rate = verified / episodes
    score = min(100, 100 * squash(rate, 0.60) + teardown_bonus)
    return score, {"n": episodes, "episodes": episodes, "verified": verified, "rate": rate,
                   "teardown_bonus": teardown_bonus}, unverified_examples[:4]


def score_toolcraft(corpus):
    total = corpus.total_tool_calls
    if total == 0:
        return 0.0, {"n": 0, "distinct": 0, "evenness": 0.0, "delegation_events": 0}, []
    # Collapse case-variant duplicates (e.g. "Bash" vs "bash") for an honest distinct count.
    merged = Counter()
    for name, cnt in corpus.tool_usage.items():
        merged[name.lower()] += cnt
    distinct = len(merged)
    breadth = squash(distinct / 20, 1.0)
    # Shannon evenness of the usage distribution.
    counts = list(merged.values())
    H = -sum((x / total) * math.log(x / total) for x in counts if x > 0)
    evenness = (H / math.log(distinct)) if distinct > 1 else 0.0
    active_hours = max(corpus.active_seconds / 3600, 0.5)
    delegation = squash(corpus.delegation_events / active_hours, 2.0)
    score = 100 * (0.45 * breadth + 0.30 * evenness + 0.25 * delegation)
    detail = {"n": total, "distinct": distinct, "evenness": evenness,
              "delegation_events": corpus.delegation_events}
    return score, detail, []


# --------------------------------------------------------------------------- #
# Aggregate: confidence shrinkage, overall score, band, archetype
# --------------------------------------------------------------------------- #

def shrink(score, n, target_n):
    c = min(1.0, n / target_n) if target_n else 1.0
    return 50 + (score - 50) * c, c


def band_for(score):
    for name, lo, hi, meaning in BANDS:
        if lo <= score <= hi:
            return name, meaning
    return BANDS[-1][0], BANDS[-1][3]


def _cosine(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def classify_archetype(dim_scores, delegation_score):
    """Nearest-prototype over your DRIVING-STYLE vector, with a margin guard.

    The vector adds a Delegation axis and is AGENCY-WEIGHTED: axes you control
    (Direction, Iteration, Toolcraft, Delegation) count fully, while axes the agent
    mostly drives on its own (Verification, Context) are heavily discounted — so the
    archetype reflects how *you* drive, not Claude's built-in habits.
    """
    scores = dict(dim_scores)
    scores["Delegation"] = delegation_score
    V = [scores[ax] for ax in ARCHETYPE_AXES]
    names = list(PROTOTYPES.keys())
    mat = [PROTOTYPES[n]["vec"] for n in names]
    # z-score each axis across prototypes + the user vector, then apply agency weights
    cols = list(zip(*(mat + [V])))
    means = [statistics.mean(col) for col in cols]
    stds = [statistics.pstdev(col) or 1.0 for col in cols]
    w = [AGENCY[ax] for ax in ARCHETYPE_AXES]

    def zw(vec):
        return [w[i] * (v - means[i]) / stds[i] for i, v in enumerate(vec)]

    vz = zw(V)
    sims = sorted(((round(_cosine(vz, zw(PROTOTYPES[n]["vec"])), 3), n) for n in names), reverse=True)
    top_sim, top = sims[0]
    second_sim, second = sims[1]
    blended = (top_sim - second_sim) < ARCHETYPE_MARGIN
    second_short = second.replace("The ", "")
    article = "an" if second_short[:1] in "AEIOU" else "a"
    return {
        "primary": top, "primary_sim": top_sim, "secondary": second, "secondary_sim": second_sim,
        "blended": blended, "all": sims, "delegation_score": round(delegation_score),
        "label": f"{PROTOTYPES[top]['emoji']} {top}" + (f", with {article} {second_short} streak" if blended else ""),
        "blurb": PROTOTYPES[top]["blurb"],
    }


# --------------------------------------------------------------------------- #
# Analysis orchestration
# --------------------------------------------------------------------------- #

def analyze(corpus):
    raw, detail, evidence = {}, {}, {}
    for name, fn in (("Direction", score_direction), ("Verification", score_verification),
                     ("Context", score_context), ("Iteration", score_iteration),
                     ("Toolcraft", score_toolcraft)):
        s, d, ev = fn(corpus)
        raw[name], detail[name], evidence[name] = s, d, ev

    shrunk, conf = {}, {}
    for name in raw:
        shrunk[name], conf[name] = shrink(raw[name], detail[name].get("n", 0), TARGET_N[name])

    overall_raw = round(sum(WEIGHTS[n] * raw[n] for n in WEIGHTS))
    overall = round(sum(WEIGHTS[n] * shrunk[n] for n in WEIGHTS))
    band, band_meaning = band_for(overall)
    # Delegation is a user-driven archetype axis (handoffs per active hour).
    active_hours = max(corpus.active_seconds / 3600, 0.5)
    delegation_score = 100 * squash(corpus.delegation_events / active_hours, 2.0)
    archetype = classify_archetype(shrunk, delegation_score)

    # length distribution of real prompts (context only)
    lens = [len(p["text"]) for p in corpus.real_prompts]
    words = [len(p["text"].split()) for p in corpus.real_prompts]
    dist = {}
    if lens:
        dist = {
            "median_chars": int(statistics.median(lens)),
            "mean_chars": int(statistics.mean(lens)),
            "median_words": int(statistics.median(words)),
            "under_80_pct": round(100 * sum(1 for L in lens if L < 80) / len(lens)),
        }

    return {
        "raw": raw, "shrunk": shrunk, "conf": conf, "detail": detail, "evidence": evidence,
        "overall_raw": overall_raw, "overall": overall, "band": band, "band_meaning": band_meaning,
        "archetype": archetype, "dist": dist,
    }


def build_action_plan(corpus, result):
    """Growth cards ranked by impact = (target - score) * weight. The teaching copy
    comes from SKILL_TEACH; user-specific evidence comes from result['evidence']."""
    TARGET = 85
    cards = []
    for name in WEIGHTS:
        score = result["shrunk"][name]
        impact = (TARGET - score) * WEIGHTS[name]
        cards.append({"dim": name, "score": round(score), "impact": impact,
                      "weak": result["evidence"].get(name, []),
                      "detail": result["detail"][name]})
    cards.sort(key=lambda c: c["impact"], reverse=True)
    # strength callout = highest shrunk score
    strength = max(WEIGHTS, key=lambda n: result["shrunk"][n])
    return cards, strength


def _shortest_action_prompt(corpus):
    cands = [p["text"] for p in corpus.real_prompts if _is_action_prompt(p["text"]) and len(p["text"]) < 40]
    return min(cands, key=len) if cands else None


def build_evidence(corpus, result, cards, archive_info=None):
    """Serialize a de-contaminated EVIDENCE bundle for the two-model analysis pipeline
    (Sonnet 4.6 explores it; Opus 4.8 analyzes it against the bundled AI-fluency
    framework). It contains your real prompts/behavior with home paths scrubbed, and is
    git-ignored. Deterministic (no randomness) so runs are reproducible."""
    prompts = corpus.real_prompts
    sample, seen = [], set()

    def add(p):
        k = (p["session"], p["idx"])
        if k in seen:
            return
        seen.add(k)
        sample.append({"text": _scrub_paths(p["text"][:600]), "project": _project_label(p["project"]),
                       "chars": len(p["text"])})

    by_len = sorted(prompts, key=lambda p: len(p["text"]))
    for p in by_len[:6]:                 # the terse nudges
        add(p)
    for p in by_len[-14:]:               # the rich, intent-carrying prompts
        add(p)
    stride = max(1, len(prompts) // 20)  # an even spread through the timeline
    for p in prompts[::stride]:
        if len(sample) >= 50:
            break
        add(p)

    def clean_ex(items):
        out = []
        for e in items or []:
            if not isinstance(e, dict):
                continue
            c = {}
            if e.get("text"):
                c["text"] = _scrub_paths(str(e["text"])[:300])
            if e.get("file"):
                c["file"] = os.path.basename(str(e["file"]))
            if e.get("files"):
                c["files"] = str(e["files"])
            if e.get("project"):
                c["project"] = _project_label(e["project"])
            if c:
                out.append(c)
        return out

    span_days = (corpus.last_ts - corpus.first_ts).days if corpus.first_ts and corpus.last_ts else 0
    a = result["archetype"]
    return {
        "schema": "claude-insight-evidence/1",
        "meta": {
            "sessions": corpus.files, "projects": len(corpus.projects),
            "real_prompts": len(prompts), "user_records": corpus.user_records,
            "filtered_noise": dict(corpus.filtered),
            "span_days": span_days,
            "active_hours": round(corpus.active_seconds / 3600, 1),
            "archive": archive_info,
            "prompt_distribution": result["dist"],
        },
        "scores": {
            "overall": result["overall"], "overall_raw": result["overall_raw"],
            "band": result["band"], "weights": WEIGHTS,
            "dimensions_raw": {k: round(v) for k, v in result["raw"].items()},
            "dimensions_adjusted": {k: round(v) for k, v in result["shrunk"].items()},
            "confidence": {k: round(v, 2) for k, v in result["conf"].items()},
            "dimension_names": DISPLAY_NAMES,
        },
        "dimension_detail": result["detail"],
        "archetype": {"primary": a["primary"], "secondary": a["secondary"],
                      "blended": a.get("blended")},
        "behavior": {
            "sample_prompts": sample,
            "weak_examples": {c["dim"]: clean_ex(c["weak"]) for c in cards},
            "tool_usage": dict(corpus.tool_usage),
            "delegation_events": corpus.delegation_events,
        },
    }


def _analysis_section_html(analysis):
    """Render the AI-authored skill map (produced by the Opus analysis stage,
    grounded in reference/ai-fluency-framework.md). Falls back to nothing if absent."""
    if not analysis or not isinstance(analysis, dict):
        return ""
    parts = ['<section><h3>Skill map — analyzed against the AI Fluency framework</h3>']
    read = analysis.get("overall_read") or analysis.get("summary")
    if read:
        parts.append(f'<p class="assess">{_esc(read)}</p>')
    for s in analysis.get("skill_map") or []:
        if not isinstance(s, dict):
            continue
        comp = _esc(s.get("competency", "?"))
        lvl = s.get("level", "?")
        label = _esc(s.get("level_label", ""))
        summ = _esc(s.get("summary", ""))
        nxt = _esc(s.get("next_move", ""))
        ev = "".join(f"<li>“{_esc(str(x)[:200])}”</li>" for x in (s.get("evidence") or [])[:3])
        parts.append(
            f'<div class="dim"><div class="dim-h"><b>{comp}</b>'
            f'<span class="pill">Level {_esc(lvl)}/5 · {label}</span></div>'
            f'<p>{summ}</p>'
            + (f'<ul class="ev">{ev}</ul>' if ev else "")
            + (f'<p class="next"><b>Your next move:</b> {nxt}</p>' if nxt else "")
            + '</div>')
    strengths = analysis.get("strengths") or []
    if strengths:
        items = "".join(f"<li>{_esc(s)}</li>" for s in strengths[:5])
        parts.append(f'<p style="margin-top:14px"><b>What you already do well:</b></p><ul class="facts">{items}</ul>')
    parts.append('<p style="color:var(--mut);font-size:13px;margin-top:10px">'
                 'This section is written by Claude Opus 4.8 from your de-contaminated evidence '
                 '(explored by Claude Sonnet 4.6), grounded in the bundled AI Fluency framework. '
                 'The numbers above are computed deterministically and independently.</p>')
    parts.append('</section>')
    return "".join(parts)


# --------------------------------------------------------------------------- #
# Reports
# --------------------------------------------------------------------------- #

def _project_label(name):
    """Claude encodes an absolute path with '-' for '/', so we can't perfectly
    recover hyphenated names. Drop the home/boilerplate prefix and show the rest.
    '-Users-me-Dropbox-AI-platzi-executive-assistant' -> 'AI platzi executive assistant'."""
    s = re.sub(r"^-?(?:Users|home)-[^-]+(?:-|$)", "", name)  # strip -Users-/-home-<user>- (mac & linux)
    s = re.sub(r"^Dropbox-", "", s)                          # strip a common cloud-folder prefix
    s = s.replace("-", " ").strip()
    # Nothing left -> the session ran in $HOME itself; never echo the raw name (it holds the username).
    if not s:
        return "home" if re.match(r"^-?(?:Users|home)-", name) else name
    return s


def terminal_summary(corpus, result):
    a = result["archetype"]
    lines = [
        "",
        f"  AI Fluency Score: {result['overall']}/100  ({result['band']})",
        f"  Archetype: {a['label']}",
        f"  Based on {len(corpus.real_prompts)} real prompts across {len(corpus.projects)} projects, "
        f"{corpus.files} sessions ({corpus.total_bytes/1e6:.1f} MB).",
        "",
    ]
    return "\n".join(lines)


def _esc(s):
    return html.escape(str(s))


# Each archetype's encouraging "next gain" — frames the top growth lever as a natural
# progression for that style rather than a deficit.
ARCH_PATHS = {
    "Autonomous Agent": "You already hand off whole jobs well — add one sharp sentence of intent per hand-off and far more will land right the first time, with less back-and-forth.",
    "Architect": "Your planning is a real strength — pair it with a quick check after each change so your designs ship proven, not just drawn.",
    "Debugger": "Your diagnostic discipline is excellent — capture each fix as a small reusable rule so the same bug never costs you twice.",
    "Collaborator": "Your back-and-forth keeps things aligned — front-loading a constraint or two will get you there in fewer rounds.",
    "Sprinter": "Your speed is real — a one-line brief plus a quick test keeps that speed from turning into rework.",
}

_SIG_DESC = {
    "Delegation": "how much you hand off — you give Claude whole jobs and trust it to run them end-to-end",
    "Toolcraft": "the range of tools you bring to bear — you reach past the shell for the right instrument",
    "Iteration": "how cleanly you change course — your corrections tend to name the fix, not just reject",
    "Briefing": "how concretely you frame requests when it matters",
}

# The specific, evidence-grounded line that explains each dimension as a growth edge.
_GROWTH_LINE = {
    "Direction": "{s}s win on how sharply they frame the work they hand off — and right now yours are often one-liners like “{ex}”, so Claude fills gaps you could have decided.",
    "Verification": "Right now changes often move on without a test, build or run to confirm them — the cheapest reliability you can buy back.",
    "Context": "Right now some edits land before the file has been read that session — an easy blind-edit risk to remove.",
    "Iteration": "Right now corrections lean toward brief rejections; naming the symptom and the exact rule resolves loops in fewer turns.",
    "Toolcraft": "Right now most work funnels through one tool — reaching for search, planning and delegation widens what you can take on.",
}


def build_assessment(corpus, result, cards):
    """A coherent, professional written read — synthesizes the numbers into one story
    and explicitly resolves the archetype-vs-weakest-dimension tension."""
    a = result["archetype"]
    arch = a["primary"]
    short = arch.replace("The ", "")
    art = "an" if short[:1] in "AEIOU" else "a"
    deleg = a["delegation_score"]
    n_deleg = corpus.delegation_events
    median = result["dist"].get("median_chars", "?")

    # signature strength = your strongest USER-driven signal (not Claude's defaults)
    user_signals = {
        "Briefing": result["shrunk"]["Direction"], "Iteration": result["shrunk"]["Iteration"],
        "Toolcraft": result["shrunk"]["Toolcraft"], "Delegation": float(deleg),
    }
    sig = max(user_signals, key=user_signals.get)

    growth = cards[0]["dim"]
    growth_disp = disp(growth)
    example = _shortest_action_prompt(corpus) or "run it"
    path_why = ARCH_PATHS.get(arch, "Keep building the habits below and your next run will show the gain.")

    p1 = (f"You drive Claude like <b>{_esc(a['label'])}</b>. {_esc(a['blurb'])} "
          f"The clearest signal is your delegation rate — <b>{deleg}/100</b>, from {n_deleg} hand-offs to "
          f"subagents, background jobs and planning — paired with fast, terse prompts (median "
          f"{median} characters).")

    p2 = (f"Your strongest <i>self-driven</i> habit is {_esc(_SIG_DESC.get(sig, sig.lower()))}. "
          f"That, plus the disciplined read→edit→verify loop your sessions show, is why your overall "
          f"score lands at <b>{result['overall']}/100 ({_esc(result['band'])})</b>.")

    gline = _GROWTH_LINE.get(growth, "").format(s=_esc(short), ex=_esc(example))
    p3 = (f"And the apparent tension, resolved: your lowest dimension is <b>{_esc(growth_disp)}</b> — but for "
          f"{art} {_esc(short)} that isn't a contradiction, it's the <i>defining</i> growth edge. {gline} "
          f"{_esc(path_why)}")

    return (f'<p class="assess">{p1}</p><p class="assess">{p2}</p><p class="assess">{p3}</p>')


# A short, punchy imperative for the hero "if you change one thing" headline — one per
# dimension, so the card adapts to whichever lever is weakest (not always Direction).
_ONE_MOVE = {
    "Direction": "Give every prompt one thing to aim at.",
    "Verification": "Check each change before you move on.",
    "Context": "Read the file before you change it.",
    "Iteration": "Correct with the exact rule, not a vibe.",
    "Toolcraft": "Reach past the shell for the right job.",
}

# The STYLE micro-key, told like a data storyteller: not "what we measured" but "what this
# reveals about how you actually work with AI" — a vivid human read, anchored in a real
# number. One per archetype; {n} = your hand-off count. This replaces the old meta-label
# ("you alone — how you drive · decisively your style") which described the yardstick,
# not the person.
_STYLE_INSIGHT = {
    "Autonomous Agent": "You run Claude like a teammate, not a tool — you hand off whole jobs and trust it to deliver. {n} of your turns set Claude loose on its own, far more than you debugged, paired, or sprinted alongside it.",
    "Architect": "You lead with a blueprint — you decide the shape of the work first and have Claude build to it, so the structure is your call, not its guess.",
    "Debugger": "You work like an investigator — you trace a failure to its root and confirm the fix before you move on, instead of patching the symptom and hoping.",
    "Collaborator": "You build shoulder-to-shoulder — you shape the answer with Claude turn by turn, steering as you go rather than handing it off and walking away.",
    "Sprinter": "You move in fast bursts — you trade long setup for momentum, shipping in quick passes and correcting on the fly instead of planning it all up front.",
}


def build_html(corpus, result, cards, strength, archive_info=None, analysis=None):
    a = result["archetype"]
    d = result["dist"]
    analysis_section = _analysis_section_html(analysis)
    days = (corpus.last_ts - corpus.first_ts).days if corpus.first_ts and corpus.last_ts else 0
    active_h = corpus.active_seconds / 3600
    filtered_total = sum(corpus.filtered.values())
    provisional = len(corpus.real_prompts) < PROVISIONAL_MIN_PROMPTS

    DIM_BLURB = {
        "Direction": "How clearly you tell the agent what you want before it acts.",
        "Verification": "Whether changes get checked (tests / build / app) before moving on.",
        "Context": "Reading a file before editing it — grounded, not blind, changes.",
        "Iteration": "Correcting precisely instead of thrashing with vague rejections.",
        "Toolcraft": "Using a healthy range of tools — not forcing everything through one.",
    }

    def dim_rate_line(name):
        det = result["detail"][name]
        if name == "Verification" and det.get("rate") is not None:
            return f"{det['verified']} of {det['episodes']} edit-bursts verified ({det['rate']*100:.0f}%)"
        if name == "Context" and det.get("rate") is not None:
            return f"{det['grounded']} of {det['total_edits']} edits were grounded in a prior read ({det['rate']*100:.0f}%)"
        if name == "Direction":
            return (f"{det['constraint_rate']*100:.0f}% carry a constraint · "
                    f"{det['artifact_rate']*100:.0f}% name a file/error · {det['intent_rate']*100:.0f}% state a why")
        if name == "Iteration":
            return f"{det['corrections']} correction turns ({det['correction_rate']*100:.0f}% of prompts); {det['specificity']*100:.0f}% were specific"
        if name == "Toolcraft":
            return f"{det.get('distinct', 0)} distinct tools, evenness {det.get('evenness', 0.0):.2f}, {det.get('delegation_events', 0)} delegations"
        return ""

    # dimension bars
    dim_html = ""
    order = sorted(WEIGHTS, key=lambda n: result["shrunk"][n], reverse=True)
    for name in order:
        sc = round(result["shrunk"][name])
        raw_sc = round(result["raw"][name])
        c = result["conf"][name]
        lowdata = c < 0.75
        tag = ""
        if name == strength:
            tag = '<span class="tag s">Strength</span>'
        elif name == cards[0]["dim"]:
            tag = '<span class="tag w">Top growth lever</span>'
        ld = '<span class="tag ld">low data</span>' if lowdata else ""
        dim_html += f"""
      <div class="dim">
        <div class="top"><span class="name">{_esc(disp(name))} {tag}{ld}</span><span class="sval">{sc}<span class="hint">/100</span></span></div>
        <div class="bar"><i style="width:{sc}%"></i></div>
        <p class="def">{_esc(DIM_BLURB[name])}</p>
        <p class="rate">{_esc(dim_rate_line(name))}<span class="wt"> · weight {int(WEIGHTS[name]*100)}%</span></p>
      </div>"""

    # --- Archetype affinity: a labelled bar per style, so the ranking is visible at a glance ---
    # Bar + number = how much your behaviour vector resembles each prototype, cosine mapped to a
    # 0–100 scale ((sim+1)/2). Every bar stays visible and the number makes the gap concrete; the
    # winner is highlighted and, for a decisive profile, leads by a wide margin. Reused in the hero.
    sims = [s for s, _ in a["all"]]
    aff_top = max(sims)

    aff = ""
    for idx, (sim, nm) in enumerate(a["all"]):
        pct = max(0, min(100, round((sim + 1) / 2 * 100)))
        win = " win" if idx == 0 else ""
        aff += (f'<div class="aff-row{win}"><div class="aff-emoji">{PROTOTYPES[nm]["emoji"]}</div>'
                f'<div class="aff-label">{_esc(nm)}</div>'
                f'<div class="aff-track"><i style="width:{pct}%"></i></div>'
                f'<div class="aff-num">{pct}</div></div>')
    # Grade the verdict by the VISIBLE gap between the top two bars, so the words match what the
    # reader sees: an 80-vs-38 lead reads "decisively / far behind", a 68-vs-52 lead reads "clearly".
    _pct = lambda s: max(0, min(100, round((s + 1) / 2 * 100)))
    _gap = _pct(a["all"][0][0]) - (_pct(a["all"][1][0]) if len(a["all"]) > 1 else 0)
    if a["blended"]:
        verdict_line = (f'<b>A blend of {_esc(a["primary"])} and {_esc(a["secondary"])}</b> — '
                        f'you switch between them depending on the task.')
        _aff_tail = "your top two are nearly tied"
    elif _gap >= 22:
        verdict_line = (f'<b>Decisively {_esc(a["primary"])}</b> — '
                        f'{_esc(a["secondary"])} is a distant second, far behind.')
        _aff_tail = "yours leads by a mile"
    else:
        verdict_line = (f'<b>Clearly {_esc(a["primary"])}</b> — '
                        f'{_esc(a["secondary"])} is your nearest other style.')
        _aff_tail = "yours is clearly out in front"
    aff += f'<p class="aff-foot">How much your style resembles each, on a 0–100 scale — {_aff_tail}.</p>'

    # 5-dot certainty meter from the winner's raw similarity (one notch softer when it's a blend).
    def _match_band(sim):
        if sim >= 0.55: return 5, "very strong"
        if sim >= 0.40: return 4, "strong"
        if sim >= 0.25: return 3, "clear"
        if sim >= 0.10: return 2, "some"
        return 1, "slight"
    _dots, match_word = _match_band(aff_top)
    if a["blended"]:
        _dots = max(1, _dots - 1)
    match_dots_html = "".join(
        ('<span class="md on"></span>' if k < _dots else '<span class="md"></span>')
        for k in range(5)
    )

    # The two hero micro-keys, written as takeaways a reader actually learns from — what the
    # number rests on, and what the style reveals — not definitions of the measurement.
    score_insight = (f"How well you and Claude work together — lifted by your "
                     f"<b>{_esc(disp(strength))}</b>, held back by your <b>{_esc(disp(cards[0]['dim']))}</b>.")
    style_insight = _STYLE_INSIGHT.get(
        a["primary"],
        "How you drive Claude, read from your prompts alone — with the reflexes Claude runs on its own set aside."
    ).format(n=corpus.delegation_events)

    # --- "If you change one thing": the single highest-leverage move, surfaced into the hero ---
    _ot = cards[0]
    _ott = SKILL_TEACH[_ot["dim"]]
    _ot_ex = _ott["examples"][0]
    one_thing_html = f"""
  <div class="one-thing">
    <div class="one-eyebrow"><span>If you change one thing</span>
      <span class="pill">{_esc(disp(_ot['dim']))} · now {_ot['score']}/100</span></div>
    <p class="one-move">{_esc(_ONE_MOVE.get(_ot['dim'], 'Make your next move deliberate.'))}</p>
    <p class="one-prac">{_esc(_ott['practice'])}</p>
    <div class="one-ba">
      <div class="ba-row was"><span>was</span><p>“{_esc(_ot_ex['before'])}”</p></div>
      <div class="ba-row now"><span>now</span><p>“{_esc(_ot_ex['after'])}”</p></div>
    </div>
    <p class="one-more">This is your highest-leverage move. <a href="#priority-1">See the full plan ↓</a></p>
  </div>"""

    # data-ingested filter breakdown
    filt = "".join(
        f"<li><b>{v:,}</b> {_esc(k)}</li>" for k, v in corpus.filtered.most_common()
    )

    # Archive stat tile + the "why ~30 days / how to see more" callout.
    archive_tile = retention_note = ""
    arch_dir_disp = _esc(archive_info["dir"]) if archive_info else _esc(DEFAULT_ARCHIVE_DIR)
    if archive_info:
        archive_tile = (f'<div class="ing"><div class="n">{archive_info["archived_sessions"]:,}</div>'
                        f'<div class="l">sessions in your archive</div></div>')
    # Show the explainer whenever the visible history is short — that's the 30-day cleanup biting.
    if days <= 32:
        grew = ""
        if archive_info and archive_info.get("enabled"):
            grew = (f' This run preserved <b>{archive_info["new"]:,}</b> new session(s) to your '
                    f'archive (<code>{arch_dir_disp}</code>), so from here your history keeps growing '
                    f'past the 30-day wall — point <code>--archive</code> at a Dropbox/iCloud folder to '
                    f'keep it across machines and reinstalls.')
        retention_note = (
            '<div class="honesty" style="margin-top:14px">'
            f'<b>Why only ~{days} days?</b> Claude Code deletes transcripts older than your '
            '<code>cleanupPeriodDays</code> setting (default <b>30</b>), so that is all that was '
            'left on disk to read — not a limit of this tool. To analyze more history: '
            '<b>(1)</b> raise <code>cleanupPeriodDays</code> in <code>~/.claude/settings.json</code> '
            '(e.g. <code>"cleanupPeriodDays": 365</code>) to stop the deletion; '
            f'<b>(2)</b> keep running Claude Insight.{grew}'
            '</div>')

    # action cards (what/where/how)
    def evidence_html(card):
        name = card["dim"]
        ev = card["weak"]
        if not ev:
            # A growth card with no single prompt to quote (e.g. Toolcraft, a distributional
            # signal): show the measured aggregate rather than a contradictory "already a habit".
            return (f'<p class="ev-agg">No single moment to point at — it shows in the aggregate: '
                    f'{_esc(dim_rate_line(name))}.</p>')
        items = ""
        # small-sample guard per project
        proj_counts = Counter(p["project"] for p in corpus.real_prompts)
        for e in ev[:3]:
            if name == "Direction" or name == "Iteration":
                proj = e["project"]; txt = _scrub_paths(e["text"])
                small = " <em>(illustrative, small sample)</em>" if proj_counts.get(proj, 0) < 10 else ""
                items += f'<li>“{_esc(txt[:140])}” <span class="loc">— {_esc(_project_label(proj))}{small}</span></li>'
            elif name == "Context":
                small = " <em>(illustrative)</em>" if proj_counts.get(e["project"], 0) < 10 else ""
                items += f'<li>Edited <code>{_esc(os.path.basename(e["file"]))}</code> without reading it first <span class="loc">— {_esc(_project_label(e["project"]))}{small}</span></li>'
            elif name == "Verification":
                small = " <em>(illustrative)</em>" if proj_counts.get(e["project"], 0) < 10 else ""
                items += f'<li>A burst of edits to <code>{_esc(e["files"])}</code> with nothing run afterwards <span class="loc">— {_esc(_project_label(e["project"]))}{small}</span></li>'
        return f"<ul class='ev'>{items}</ul>"

    cards_html = ""
    for i, card in enumerate(cards[:2]):
        name = card["dim"]
        id_attr = ' id="priority-1"' if i == 0 else ''
        t = SKILL_TEACH[name]
        ex_html = "".join(
            f'<div class="ba"><div class="before"><span>Instead of</span>“{_esc(e["before"])}”</div>'
            f'<div class="after"><span>Stronger</span>“{_esc(e["after"])}”</div></div>'
            for e in t["examples"]
        )
        cards_html += f"""
      <div class="card prio"{id_attr}>
        <div class="ph">Priority {i+1} · {_esc(disp(name))} <span class="pscore">now {card['score']}/100</span></div>
        <h4>{_esc(t['what_it_is'])}</h4>
        <p class="why"><b>Why it matters.</b> {_esc(t['why_it_matters'])}</p>
        <div class="wwh"><span class="lab">Where this shows up in your sessions</span>{evidence_html(card)}</div>
        <div class="wwh"><span class="lab">How to grow it</span><p class="how">{_esc(t['how_to_improve'])}</p>
          {ex_html}
        </div>
        <p class="tgt">🎯 Try this next session: {_esc(t['practice'])}</p>
      </div>"""

    # strength callout — lead with the user's signature (self-driven) strength
    s_det = dim_rate_line(strength)
    strength_html = f"""
      <div class="card keep">
        <div class="ph">Keep doing this · {_esc(disp(strength))} <span class="pscore">{round(result['shrunk'][strength])}/100</span></div>
        <p>{_esc(SKILL_TEACH[strength]['good_looks_like'])} The evidence in your sessions: {_esc(s_det)}. This is your foundation — build on it.</p>
      </div>"""

    # skill map (levels)
    skill_levels = _skill_levels(result)
    skill_html = ""
    for sk in skill_levels:
        dots = "".join(
            f'<span class="dot {"on" if i < sk["level"] else ""}"></span>' for i in range(5)
        )
        skill_html += f"""<div class="skill">
          <div class="sk-top"><span class="sk-name">{_esc(sk['name'])} <span class="lvl">Level {sk['level']}/5</span></span><span class="sk-dots">{dots}</span></div>
          <p class="sk-what">{_esc(sk['what'])}</p>
          <p class="sk-now"><b>You're here:</b> {_esc(sk['now'])}</p>
          <p class="sk-next"><b>Next move:</b> {_esc(sk['next'])}</p></div>"""

    prov_banner = ""
    if provisional:
        prov_banner = (f'<div class="prov">⚠️ Provisional: only {len(corpus.real_prompts)} real prompts found — '
                       f'treat the score as a rough range (±10). It sharpens as you use Claude Code more.</div>')

    # fun facts
    facts = [
        f"{len(corpus.real_prompts)} prompts you actually typed (out of {corpus.user_records:,} user records — the rest were tool output, subagent turns or system text)",
        f"median prompt is {d.get('median_chars','?')} characters ({d.get('median_words','?')} words); {d.get('under_80_pct','?')}% are under 80 chars",
        f"{active_h:.0f} hours of hands-on time (idle gaps over 5 min excluded)",
        f"{result['detail']['Toolcraft']['distinct']} distinct tools used; {corpus.total_tool_calls:,} tool calls in total",
        f"most-used tool: {corpus.tool_usage.most_common(1)[0][0] if corpus.tool_usage else 'n/a'}",
        f"{corpus.delegation_events} delegations (subagents / background jobs / planning)",
    ]
    facts_html = "".join(f"<li>{_esc(f)}</li>" for f in facts)
    assessment_html = build_assessment(corpus, result, cards)

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Your AI Fluency Report</title>
<style>
:root{{--bg:#0c0d18;--p:#15172a;--p2:#1d2040;--ink:#eef0ff;--mut:#a4a8cc;--line:#2a2d52;
--ac:#7c5cff;--ac2:#3ad6c9;--good:#3ad68a;--warn:#ffb454;--bad:#ff6b8b;}}
*{{box-sizing:border-box;margin:0;padding:0}}
body{{background:radial-gradient(1100px 640px at 72% -12%,#262a55 0%,var(--bg) 55%);color:var(--ink);
font:16px/1.65 -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;padding-bottom:80px}}
.wrap{{max-width:880px;margin:0 auto;padding:0 22px}}
header{{text-align:center;padding:60px 0 12px}}
.kick{{letter-spacing:.22em;text-transform:uppercase;font-size:12px;color:var(--mut)}}
h1{{font-size:34px;margin:10px 0 4px}}
.sub{{color:var(--mut);max-width:620px;margin:6px auto 0;font-size:15px}}
.hero{{margin:30px auto 0;display:flex;gap:22px;align-items:stretch;flex-wrap:wrap;justify-content:center}}
.score-card{{background:linear-gradient(135deg,var(--p2),var(--p));border:1px solid var(--line);border-radius:22px;
padding:26px 30px;text-align:center;flex:0 1 300px;min-width:240px;max-width:320px;box-shadow:0 18px 50px rgba(0,0,0,.4);
display:flex;flex-direction:column;justify-content:center;align-items:center}}
.score-card .key{{align-items:center}}
.ring{{position:relative;width:170px;height:170px;margin:0 auto}}
.ring .n{{position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;justify-content:center}}
.ring .n b{{font-size:50px;line-height:1}}
.ring .n s{{text-decoration:none;color:var(--mut);font-size:13px}}
.band{{margin-top:12px;font-size:19px;font-weight:700;color:var(--ac2)}}
.rawnote{{color:var(--mut);font-size:12px;margin-top:4px}}
.arch{{flex:1;min-width:260px;background:var(--p);border:1px solid var(--line);border-radius:22px;padding:24px 26px;text-align:left;display:flex;flex-direction:column}}
.prov{{background:rgba(255,180,84,.1);border:1px solid rgba(255,180,84,.35);color:#ffe6c2;border-radius:12px;padding:12px 16px;margin:22px 0 0;font-size:14px}}
section{{margin:42px 0}}
h3{{font-size:13px;letter-spacing:.16em;text-transform:uppercase;color:var(--mut);border-bottom:1px solid var(--line);padding-bottom:10px;margin-bottom:18px}}
.band-meaning{{background:var(--p);border:1px solid var(--line);border-left:4px solid var(--ac);border-radius:12px;padding:16px 20px;color:#dfe2ff}}
.assess{{background:var(--p);border:1px solid var(--line);border-radius:14px;padding:16px 20px;margin-bottom:12px;font-size:15.5px;line-height:1.7;color:#e8eaff}}
.assess b{{color:#fff}}
.ingest{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px}}
.ing{{background:var(--p);border:1px solid var(--line);border-radius:14px;padding:14px 16px}}
.ing .n{{font-size:24px;font-weight:700;color:var(--ac2)}}
.ing .l{{color:var(--mut);font-size:13px;margin-top:2px}}
.honesty{{margin-top:16px;background:var(--p);border:1px solid var(--line);border-radius:14px;padding:16px 20px}}
.honesty b{{color:var(--ink)}}
.honesty ul{{list-style:none;display:flex;flex-wrap:wrap;gap:8px 22px;margin-top:8px}}
.honesty li{{color:var(--mut);font-size:14px}}
.dim{{background:var(--p);border:1px solid var(--line);border-radius:14px;padding:16px 20px;margin-bottom:12px}}
.dim .top{{display:flex;justify-content:space-between;align-items:baseline}}
.dim .name{{font-weight:700;font-size:17px}}
.dim .sval{{font-size:22px;font-weight:800}} .dim .hint{{color:var(--mut);font-size:12px;font-weight:400}}
.dim-h{{display:flex;justify-content:space-between;align-items:baseline;gap:12px;margin-bottom:6px}}
.dim-h b{{font-size:17px}}
.pill{{font-size:12px;font-weight:700;color:var(--ink);background:var(--p2);border:1px solid var(--line);border-radius:99px;padding:3px 11px;white-space:nowrap}}
.ev{{margin:8px 0 0 0;padding-left:18px}} .ev li{{color:var(--mut);font-size:14px;margin:3px 0}}
.next{{margin-top:8px;font-size:14.5px}} .next b{{color:#fff}}
.bar{{height:9px;background:#23264a;border-radius:99px;overflow:hidden;margin:11px 0 9px}}
.bar>i{{display:block;height:100%;border-radius:99px;background:linear-gradient(90deg,var(--ac),var(--ac2))}}
.def{{color:var(--ink);font-size:14.5px}} .rate{{color:var(--mut);font-size:13px;margin-top:3px}} .wt{{opacity:.7}}
.tag{{font-size:10.5px;padding:2px 8px;border-radius:99px;font-weight:700;margin-left:6px;vertical-align:middle}}
.tag.s{{background:rgba(58,214,138,.16);color:var(--good)}} .tag.w{{background:rgba(255,107,139,.16);color:var(--bad)}}
.tag.ld{{background:rgba(164,168,204,.16);color:var(--mut)}}
.card{{background:var(--p);border:1px solid var(--line);border-radius:16px;padding:18px 22px;margin-bottom:14px}}
.prio{{border-left:4px solid var(--warn)}} .keep{{border-left:4px solid var(--good)}}
.ph{{font-size:12px;text-transform:uppercase;letter-spacing:.1em;color:var(--mut)}}
.pscore{{float:right;color:var(--ac2);letter-spacing:0}}
.card h4{{font-size:18px;margin:8px 0 12px}}
.wwh{{margin:12px 0}} .wwh .lab{{display:block;font-size:12px;text-transform:uppercase;letter-spacing:.08em;color:var(--mut);margin-bottom:6px}}
ul.ev{{list-style:none}} ul.ev li{{background:var(--p2);border-radius:9px;padding:9px 12px;margin-bottom:7px;font-size:14px}}
.loc{{color:var(--mut);font-size:12.5px}} .ev-none{{color:var(--good);font-size:14px}}
.ev-agg{{color:var(--mut);font-size:14px}}
.ba{{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:8px}}
.why{{color:var(--mut);font-size:14px;margin:2px 0 4px}} .why b{{color:var(--ink)}}
.how{{font-size:14.5px;margin:0 0 4px}}
.sk-what{{color:var(--ink);font-size:13.5px;margin-top:5px}}
.lvl{{font-size:11px;color:var(--ac2);font-weight:600;margin-left:6px}}
.before,.after{{border-radius:10px;padding:10px 13px;font-size:14px}}
.before{{background:rgba(255,107,139,.08);color:#ffd0da}} .after{{background:rgba(58,214,138,.08);color:#cfeede}}
.before span,.after span{{display:block;font-size:11px;text-transform:uppercase;letter-spacing:.08em;opacity:.7;margin-bottom:3px}}
.tgt{{margin-top:10px;color:var(--ac2);font-size:14px}}
.skill{{background:var(--p);border:1px solid var(--line);border-radius:14px;padding:14px 18px;margin-bottom:10px}}
.sk-top{{display:flex;justify-content:space-between;align-items:center}} .sk-name{{font-weight:700}}
.dot{{display:inline-block;width:11px;height:11px;border-radius:50%;background:#2a2d52;margin-left:4px}}
.dot.on{{background:linear-gradient(135deg,var(--ac),var(--ac2))}}
.sk-now{{color:var(--mut);font-size:13.5px;margin-top:6px}} .sk-next{{font-size:13.5px;margin-top:3px}}
.facts{{list-style:none}} .facts li{{background:var(--p);border:1px solid var(--line);border-radius:10px;padding:11px 15px;margin-bottom:8px;font-size:14.5px}}
.facts li::before{{content:"›";color:var(--ac2);font-weight:800;margin-right:9px}}
details{{background:var(--p);border:1px solid var(--line);border-radius:12px;padding:14px 18px;margin-top:14px}}
summary{{cursor:pointer;color:var(--mut);font-size:14px}} details p,details li{{color:var(--mut);font-size:13px;margin-top:8px}}
footer{{text-align:center;color:var(--mut);font-size:13px;margin-top:46px}}
code{{background:#23264a;padding:1px 6px;border-radius:5px;font-size:13px}}
/* hero — score-vs-style micro-keys */
.key{{display:flex;flex-direction:column;font-size:13.5px;color:#c4c9ec;text-align:left;margin-top:16px;line-height:1.5}}
.key>b{{font-size:11px;letter-spacing:.1em;text-transform:uppercase;margin-bottom:5px;color:var(--mut);font-weight:700}}
.key span b{{color:var(--ink);font-weight:700}}
.key-score{{text-align:center}} .key-score>b{{color:var(--ac2)}}
.key-style{{border-top:1px solid var(--line);padding-top:14px;margin-top:16px}} .key-style>b{{color:var(--ac2)}}
/* hero — driving-style spread + winner-relative affinity */
.arch-eyebrow{{display:flex;justify-content:space-between;align-items:baseline;gap:12px;text-transform:uppercase;letter-spacing:.16em;font-size:11px;color:var(--mut)}}
.arch-name{{font-size:28px;font-weight:800;color:var(--ink);margin:6px 0 0}}
.arch-rule{{height:2px;width:100%;border-radius:2px;margin:8px 0 12px;background:linear-gradient(90deg,var(--ac),var(--ac2))}}
.lede{{color:var(--ink);font-size:16px;line-height:1.55}}
.match-dots{{display:inline-flex;align-items:center;gap:5px}}
.match-dots .md{{width:7px;height:7px;border-radius:50%;background:var(--line);display:inline-block}}
.match-dots .md.on{{background:linear-gradient(135deg,var(--ac),var(--ac2))}}
.match-word{{font-style:normal;letter-spacing:0;text-transform:none;color:var(--ink);font-size:12px;margin-left:6px}}
.affinity{{margin:14px 0 4px}}
.aff-row{{display:flex;align-items:center;gap:10px;margin:6px 0}}
.aff-emoji{{width:20px;text-align:center;font-size:15px;opacity:.6}}
.aff-label{{min-width:130px;font-size:13px;color:var(--mut)}}
.aff-track{{flex:1;height:7px;background:#23264a;border-radius:99px;overflow:hidden}}
.aff-track>i{{display:block;height:100%;border-radius:99px;background:linear-gradient(90deg,#6f63d8,#8f86e6);opacity:.85}}
.aff-num{{min-width:30px;text-align:right;font-size:13px;color:var(--mut);font-variant-numeric:tabular-nums}}
.aff-row.win .aff-emoji{{opacity:1}}
.aff-row.win .aff-label{{color:var(--ink);font-size:16px;font-weight:700}}
.aff-row.win .aff-track>i{{background:linear-gradient(90deg,var(--ac),var(--ac2));opacity:1}}
.aff-row.win .aff-num{{color:var(--ink);font-weight:700}}
.aff-foot{{color:var(--mut);font-size:12px;margin-top:8px}}
.verdict{{font-size:13px;color:var(--mut);margin-top:6px}} .verdict b{{color:var(--ink)}}
/* hero — "if you change one thing" full-width band */
.one-thing{{flex-basis:100%;border-top:1px solid var(--line);padding-top:22px;margin-top:6px;text-align:left}}
.one-eyebrow{{display:flex;justify-content:space-between;align-items:baseline;gap:12px;text-transform:uppercase;letter-spacing:.16em;font-size:11px;color:var(--mut);margin-bottom:12px}}
.one-move{{font-size:22px;font-weight:700;color:var(--ink);line-height:1.3}}
.one-prac{{color:var(--mut);font-size:15px;margin-top:4px}}
.one-ba{{display:grid;gap:8px;margin:16px 0 4px}}
.ba-row{{display:grid;grid-template-columns:46px 1fr;gap:12px;align-items:baseline;border-radius:10px;padding:10px 13px;font-size:14px}}
.ba-row>span{{text-transform:uppercase;letter-spacing:.08em;font-size:11px;font-weight:700;opacity:.85}}
.ba-row.was{{background:rgba(255,107,139,.08)}} .ba-row.was>span{{color:var(--bad)}} .ba-row.was p{{color:#ffd0da}}
.ba-row.now{{background:rgba(58,214,138,.08)}} .ba-row.now>span{{color:var(--good)}} .ba-row.now p{{color:#cfeede}}
.one-more{{font-size:13px;color:var(--mut);margin-top:10px}}
.one-more a{{color:var(--ac2);text-decoration:none}} .one-more a:hover{{text-decoration:underline}}
@media(max-width:640px){{.ba{{grid-template-columns:1fr}}.arch-name{{font-size:24px}}.arch-eyebrow{{flex-wrap:wrap;gap:6px}}.aff-label{{min-width:96px}}.one-move{{font-size:20px}}}}
</style></head><body><div class="wrap">

<header>
  <div class="kick">Claude Insight · AI Fluency Report</div>
  <h1>How skillfully you build with AI</h1>
  <p class="sub">A read of how you actually drive Claude Code — measured from your real prompts and Claude's real actions, analyzed entirely on your machine.</p>
</header>

{prov_banner}

<div class="hero">
  <div class="score-card">
    <div class="ring">
      <svg width="170" height="170" style="transform:rotate(-90deg)">
        <circle cx="85" cy="85" r="74" fill="none" stroke="#23264a" stroke-width="12"/>
        <circle cx="85" cy="85" r="74" fill="none" stroke="url(#g)" stroke-width="12" stroke-linecap="round"
          stroke-dasharray="{2*math.pi*74*result['overall']/100:.0f} 999"/>
        <defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="#7c5cff"/><stop offset="1" stop-color="#3ad6c9"/></linearGradient></defs>
      </svg>
      <div class="n"><b>{result['overall']}</b><s>/ 100</s></div>
    </div>
    <div class="band">{_esc(result['band'])}</div>
    <div class="rawnote">raw {result['overall_raw']} · confidence-adjusted {result['overall']}</div>
    <div class="key key-score"><b>What the {result['overall']} means</b><span>{score_insight}</span></div>
  </div>
  <div class="arch">
    <div class="arch-eyebrow"><span>Your driving style</span>
      <span class="match-dots">{match_dots_html}<i class="match-word">{match_word} match</i></span></div>
    <h2 class="arch-name">{PROTOTYPES[a['primary']]['emoji']} {_esc(a['primary'])}</h2>
    <div class="arch-rule"></div>
    <p class="lede">{_esc(a['blurb'])}</p>
    <div class="affinity">{aff}</div>
    <p class="verdict">{verdict_line}</p>
    <div class="key key-style"><b>What your style means</b><span>{style_insight}</span></div>
  </div>
  {one_thing_html}
</div>

<section>
  <h3>Professional assessment</h3>
  {assessment_html}
</section>

<section>
  <h3>What your score means</h3>
  <div class="band-meaning"><b>{_esc(result['band'])} ({result['overall']}/100).</b> {_esc(result['band_meaning'])}</div>
</section>

<section>
  <h3>How much data this is based on</h3>
  <div class="ingest">
    <div class="ing"><div class="n">{corpus.files}</div><div class="l">sessions scanned</div></div>
    <div class="ing"><div class="n">{len(corpus.projects)}</div><div class="l">projects</div></div>
    <div class="ing"><div class="n">{corpus.total_bytes/1e6:.1f} MB</div><div class="l">transcript data parsed</div></div>
    <div class="ing"><div class="n">{days} days</div><div class="l">span of activity</div></div>
    <div class="ing"><div class="n">{len(corpus.real_prompts)}</div><div class="l">real prompts you typed</div></div>
    <div class="ing"><div class="n">{active_h:.0f} h</div><div class="l">hands-on active time</div></div>
    {archive_tile}
  </div>
  {retention_note}
  <div class="honesty">
    <b>The honest part:</b> we found {corpus.user_records:,} “user” records but only <b>{len(corpus.real_prompts)}</b> are prompts <b>you</b> typed. We filtered out {filtered_total:,} that the old tool wrongly counted:
    <ul>{filt}</ul>
    <p style="color:var(--mut);font-size:13px;margin-top:10px">Your real prompts: median {d.get('median_chars','?')} chars · {d.get('under_80_pct','?')}% under 80 chars · {active_h:.0f} h hands-on active time (idle gaps over 5 min are excluded — not raw wall-clock).</p>
  </div>
</section>

{analysis_section}

<section>
  <h3>The five dimensions</h3>
  {dim_html}
</section>

<section>
  <h3>What to improve — and exactly how</h3>
  {cards_html}
  {strength_html}
</section>

<section>
  <h3>Your skill map</h3>
  {skill_html}
</section>

<section>
  <h3>Honest numbers at a glance</h3>
  <ul class="facts">{facts_html}</ul>
</section>

<section>
  <h3>Methodology &amp; honesty</h3>
  <details><summary>How every number was computed (click to expand)</summary>
    <p><b>Only real prompts are scored.</b> A “user” record counts as a prompt only if it is not a tool-result, not a subagent (sidechain) turn, not meta/injected, not a slash-command stub, and not a paste/system-prompt over {MAX_HUMAN_PROMPT_CHARS:,} chars or opening with “You are …”. This removes the contamination that made the old tool report a {d.get('mean_chars','?')}-vs-real average.</p>
    <p><b>Everything is a rate, then squashed.</b> Each dimension is a per-prompt or per-opportunity rate run through min(1, rate/target), so doing more work never raises the score — only doing it better does. Weights: Briefing 24%, Verification 22%, Context-setting 22%, Iteration 18%, Toolcraft 14%.</p>
    <p><b>Thin signals are hedged, not faked.</b> Each dimension is pulled toward a neutral 50 in proportion to how many opportunities it had (e.g. Iteration had only {result['detail']['Iteration']['corrections']} corrections, so it is flagged “low data”). Both raw and confidence-adjusted scores are shown.</p>
    <p><b>Archetype</b> describes your <b>driving style</b>, not the collaboration's quality, so it is built on a separate <b>agency-weighted</b> vector: Briefing, Iteration, Toolcraft and Delegation (handoffs to subagents/background jobs/planning) count fully, while Verification and Context — habits Claude largely does on its own — are discounted ({int(AGENCY['Verification']*100)}% and {int(AGENCY['Context']*100)}% weight). It is the nearest prototype by cosine on z-scored values; if the top two are within {ARCHETYPE_MARGIN} we show a blend. <b>Active time</b> caps idle gaps at {GAP_CAP_SECONDS//60} min. <b>Fixes vs v1:</b> prompt mis-count, length inflation, idle-time over-count, random archetype, uncapped tool-diversity, and keyword “error” false-positives.</p>
    <p><b>Limits:</b> this measures observable behavior, not intent; detectors are heuristic and English-biased; it's a single snapshot, not a trend. Terse prompts that carry intent from the prior turn can under-score Direction.</p>
  </details>
</section>

<footer>Generated locally by Claude Insight v2 · your transcripts never left this machine.</footer>
</div></body></html>"""


def _skill_levels(result):
    """Map dimension scores to L1-L5 skill levels with now/next text."""
    def lvl(score):
        return max(1, min(5, int(score // 20) + 1))
    s = result["shrunk"]
    defs = [
        ("Briefing & specificity", "Direction",
         "name a goal + one anchor (path, constraint, or acceptance test) in most action prompts",
         {1: "Mostly short nudges with little context.", 2: "Occasional context; one constraint sometimes.",
          3: "Most prompts carry a goal + one anchor.", 4: "Goal + constraint + criterion are common.",
          5: "Consistently high-context with front-loaded rules."}),
        ("Verification discipline", "Verification",
         "end edit-bursts by running the tests / the app before moving on",
         {1: "Edits accepted blind, almost no checks.", 2: "Verifies occasionally.",
          3: "Verifies most bursts of edits.", 4: "Verifies nearly every change.",
          5: "Verification is a reflex — stated up front and layered."}),
        ("Context grounding (read→edit)", "Context",
         "have the agent read the target file before changing it",
         {1: "Often edits files it never read.", 2: "Reads before editing about half the time.",
          3: "Usually points the agent at the right place first.", 4: "Routinely reads target + deps before changing.",
          5: "Deliberate exploration before non-trivial changes."}),
        ("Iteration & recovery", "Iteration",
         "make corrections name a symptom + the exact rule, in one line",
         {1: "Low-info rejections, long loops.", 2: "Corrects but vaguely.",
          3: "Mixes precise and bare corrections.", 4: "Low correction rate, mostly specific.",
          5: "Surgical feedback; turns misses into reusable rules."}),
        ("Toolcraft & orchestration", "Toolcraft",
         "reach past the shell — search, planning, delegation for the right jobs",
         {1: "Effectively one tool.", 2: "The core trio (Bash/Read/Edit).",
          3: "Adds search/web and some planning.", 4: "Comfortable with MCP + balanced spread.",
          5: "20+ tools used appropriately, low concentration."}),
    ]
    out = []
    for name, dim, nxt, rub in defs:
        L = lvl(s[dim])
        out.append({"name": name, "dim": dim, "level": L, "now": rub[L],
                    "what": SKILL_TEACH[dim]["what_it_is"],
                    "next": nxt if L < 5 else "maintain this — it's a real strength."})
    return out


# --------------------------------------------------------------------------- #
# Demo mode — render the exact report design from a fictional developer ("Sam"),
# so the template can be shown or shared with ZERO real data. It fabricates
# Claude Code transcripts and runs them through the *real* pipeline (same code
# path as a live run), so the sample is guaranteed to match a genuine report.
# --------------------------------------------------------------------------- #

# Folder names mimic Claude Code's project dirs; _project_label turns these into
# "acme api", "web store", "cli tools".
_DEMO_PROJECTS = {
    "acme-api":  "-Users-sam-acme-api",
    "web-store": "-Users-sam-web-store",
    "cli-tools": "-Users-sam-cli-tools",
    "data-pipe": "-Users-sam-data-pipe",
}

# Each session is (project, [steps]). A step is a tuple whose first item is a kind:
#   ("p", text)  human prompt        ("r", path) Read     ("e", path) Edit
#   ("w", path)  Write (new file)    ("b", cmd)  Bash     ("g", query) Grep
#   ("glob", q)  Glob                ("plan",)   ExitPlanMode   ("task", desc) Task
# The mix is authored to read as an "Architect": strong, anchored briefs, read-before-edit
# as a habit, verification most of the time (a few bursts left unverified for honest growth
# evidence), precise corrections, and only light delegation.
def _demo_sessions():
    return [
        ("acme-api", [
            ("p", "Before we touch anything, plan how to add request retries to api/client.py — the goal is exponential backoff so a flaky upstream doesn't fail the whole call."),
            ("plan",),
            ("r", "api/client.py"),
            ("p", "Add a retry wrapper in api/client.py: retry GET up to 3 times with backoff, but never retry POST so we don't double-charge. Keep the public signature unchanged."),
            ("e", "api/client.py"),
            ("b", "pytest tests/test_client.py -q"),
            ("p", "Add a test in tests/test_client.py that asserts the 3rd retry succeeds and POST is never retried, so this stays locked in."),
            ("r", "tests/test_client.py"),
            ("e", "tests/test_client.py"),
            ("b", "pytest -q"),
        ]),
        ("acme-api", [
            ("p", "Users stay logged out after a correct password. Read api/auth/session.py and tell me why the cookie isn't being set."),
            ("r", "api/auth/session.py"),
            ("p", "Fix it in api/auth/session.py so a valid login sets the session cookie with Secure and HttpOnly, and keep the existing tests green."),
            ("e", "api/auth/session.py"),
            ("b", "pytest tests/test_auth.py -q"),
            ("p", "no, that's still failing on Safari — the SameSite attribute must be 'None' for cross-site, fix that exact line and nothing else."),
            ("e", "api/auth/session.py"),
            ("b", "pytest tests/test_auth.py -q"),
        ]),
        ("web-store", [
            ("p", "Add a cart badge to web/components/Header.tsx that shows the item count and hides at zero so it isn't noisy."),
            ("e", "web/components/Header.tsx"),
            ("e", "web/components/Cart.tsx"),
            ("p", "Now wire it to the cart store in web/store/cart.ts so the badge reflects add and remove without a page refresh."),
            ("r", "web/store/cart.ts"),
            ("e", "web/store/cart.ts"),
            ("b", "npm run test"),
        ]),
        ("web-store", [
            ("p", "Plan the checkout validation in web/checkout/validate.ts before coding — the goal is to block empty carts and expired coupons."),
            ("plan",),
            ("g", "validateCoupon"),
            ("r", "web/checkout/validate.ts"),
            ("p", "Implement validate.ts: reject empty carts and expired coupons, and return a typed error so the UI can show a message. Don't change the happy path."),
            ("e", "web/checkout/validate.ts"),
            ("b", "npm run test -- checkout"),
        ]),
        ("cli-tools", [
            ("p", "Refactor cli/format.py to split the table renderer into its own module cli/render.py so it's testable in isolation."),
            ("glob", "cli/*.py"),
            ("r", "cli/format.py"),
            ("w", "cli/render.py"),
            ("e", "cli/format.py"),
            ("b", "pytest tests/test_render.py -q"),
            ("p", "Add cli/render.py tests covering wide tables and empty tables so those edge cases don't regress."),
            ("w", "tests/test_render.py"),
            ("b", "pytest -q"),
        ]),
        ("cli-tools", [
            ("p", "fix the output"),
            ("r", "cli/output.py"),
            ("e", "cli/output.py"),
            ("p", "make it faster"),
            ("e", "cli/output.py"),
            ("p", "Use a subagent to update the docstrings across cli/ to match the new render API, so the docs don't drift."),
            ("task", "update docstrings in cli/"),
            ("p", "Run the linter on cli/ and fix what it flags so CI passes."),
            ("b", "ruff check cli/"),
        ]),
        ("acme-api", [
            ("p", "Add structured logging to api/server.py using the existing logger so each request records method, path and latency — don't log request bodies."),
            ("r", "api/server.py"),
            ("e", "api/server.py"),
            ("b", "pytest -q"),
            ("p", "Add a /healthz route in api/server.py that returns 200 so the load balancer can probe it."),
            ("e", "api/server.py"),
            ("b", "curl -s localhost:8000/healthz"),
        ]),
        ("web-store", [
            ("p", "Add optimistic UI to web/components/Cart.tsx so removing an item updates instantly and rolls back if the request fails."),
            ("r", "web/components/Cart.tsx"),
            ("e", "web/components/Cart.tsx"),
            ("p", "speed it up"),
            ("e", "web/components/Cart.tsx"),
            ("p", "Memoize the cart selector in web/store/cart.ts so the header doesn't re-render on every keystroke."),
            ("r", "web/store/cart.ts"),
            ("e", "web/store/cart.ts"),
            ("b", "npm run test -- cart"),
        ]),
        ("cli-tools", [
            ("p", "Plan a --json flag for cli/main.py before building — the goal is machine-readable output for scripts without breaking the human table."),
            ("plan",),
            ("r", "cli/main.py"),
            ("p", "Implement the --json flag in cli/main.py so it emits the same data as JSON, and keep the default table output unchanged."),
            ("e", "cli/main.py"),
            ("b", "pytest tests/test_cli.py -q"),
            ("p", "Add a test in tests/test_cli.py asserting --json round-trips to the same rows, so the contract is covered."),
            ("w", "tests/test_cli.py"),
            ("b", "pytest -q"),
        ]),
        ("data-pipe", [
            ("p", "Plan the nightly ETL in data/pipeline.py before we build — the goal is idempotent loads so a re-run never duplicates rows."),
            ("plan",),
            ("todo",),
            ("r", "data/pipeline.py"),
            ("p", "Implement the loader in data/pipeline.py: upsert on the natural key so re-runs are idempotent, and batch in chunks of 1000 so memory stays flat."),
            ("e", "data/pipeline.py"),
            ("b", "pytest tests/test_pipeline.py -q"),
            ("p", "no, the upsert duplicates rows when the key is null — treat null keys as a skip instead, and add a skipped-count to the summary."),
            ("e", "data/pipeline.py"),
            ("b", "pytest tests/test_pipeline.py -q"),
        ]),
        ("data-pipe", [
            ("p", "Use a subagent to add type hints across data/ and fix whatever mypy flags, so the whole package type-checks cleanly."),
            ("task", "add type hints across data/"),
            ("ws", "mypy strict optional handling"),
            ("b", "mypy data/"),
            ("p", "Spin up the local stack in the background so I can hit it while we keep working."),
            ("bgb", "docker compose up"),
            ("p", "make the loader logs quieter"),
            ("r", "data/pipeline.py"),
            ("e", "data/pipeline.py"),
        ]),
        ("web-store", [
            ("p", "The product page feels slow. Profile web/pages/product.tsx and tell me the top cost before we change anything."),
            ("r", "web/pages/product.tsx"),
            ("p", "Memoize the price formatter and lazy-load the reviews in web/pages/product.tsx so first paint isn't blocked, and keep SSR working."),
            ("m", "web/pages/product.tsx"),
            ("p", "too aggressive — lazy-loading the reviews broke the SEO snapshot; keep reviews server-rendered and only defer the image gallery."),
            ("e", "web/pages/product.tsx"),
            ("bgb", "npm run dev"),
            ("p", "add a perf test"),
            ("e", "web/components/Gallery.tsx"),
            ("b", "npm run test -- product"),
        ]),
        ("acme-api", [
            ("p", "Add a Stripe webhook handler in api/webhooks/stripe.py that verifies the signature and is idempotent on event id, so a retried event never double-processes."),
            ("wf", "https://stripe.com/docs/webhooks/signatures"),
            ("r", "api/webhooks/stripe.py"),
            ("e", "api/webhooks/stripe.py"),
            ("b", "pytest tests/test_webhooks.py -q"),
            ("p", "Use a subagent to wire the same idempotency guard into the other three webhook handlers so they all behave consistently."),
            ("task", "apply idempotency to remaining webhooks"),
            ("p", "Add tests in tests/test_webhooks.py for a replayed event and a bad signature so both paths are covered."),
            ("w", "tests/test_webhooks.py"),
            ("b", "pytest -q"),
        ]),
        ("cli-tools", [
            ("p", "Plan a config loader for cli/config.py before coding — precedence should be flag, then env, then file, so the most explicit value wins."),
            ("plan",),
            ("g", "load_config"),
            ("r", "cli/config.py"),
            ("p", "Implement cli/config.py with that precedence, fall back to sane defaults, and don't read anything off the network."),
            ("e", "cli/config.py"),
            ("b", "pytest tests/test_config.py -q"),
            ("p", "actually a missing config file should be silent, not a warning — only warn when the file exists but is malformed."),
            ("e", "cli/config.py"),
            ("b", "pytest -q"),
        ]),
        ("web-store", [
            ("p", "Add a wishlist in web/components/Wishlist.tsx backed by web/store/wishlist.ts so saved items persist across sessions in localStorage."),
            ("r", "web/store/wishlist.ts"),
            ("w", "web/components/Wishlist.tsx"),
            ("e", "web/store/wishlist.ts"),
            ("b", "npm run test -- wishlist"),
            ("p", "Use a subagent to add the wishlist button to every product card so it's consistent across the whole catalog."),
            ("task", "add wishlist button to product cards"),
            ("p", "clean it up"),
            ("m", "web/components/Wishlist.tsx"),
            ("b", "npm run build"),
        ]),
        ("data-pipe", [
            ("p", "Plan metrics for data/pipeline.py before adding them — the goal is rows-processed and failures-per-run, scraped by Prometheus."),
            ("plan",),
            ("ws", "prometheus python client counter vs gauge"),
            ("r", "data/pipeline.py"),
            ("p", "Add a Prometheus counter for processed rows and a gauge for last-run duration in data/pipeline.py, and don't block the load if the metrics push fails."),
            ("e", "data/pipeline.py"),
            ("bgb", "docker compose up prometheus"),
            ("p", "Add a test in tests/test_metrics.py asserting the counter increments per row so it can't silently break."),
            ("w", "tests/test_metrics.py"),
            ("b", "pytest -q"),
        ]),
    ]


def _write_demo_transcripts(root):
    """Materialize the fictional sessions as Claude Code JSONL transcripts under `root`."""
    builders = {
        "r": lambda a: ("Read", {"file_path": a}),
        "e": lambda a: ("Edit", {"file_path": a}),
        "m": lambda a: ("MultiEdit", {"file_path": a}),
        "w": lambda a: ("Write", {"file_path": a}),
        "b": lambda a: ("Bash", {"command": a}),
        "bgb": lambda a: ("Bash", {"command": a, "run_in_background": True}),
        "g": lambda a: ("Grep", {"pattern": a}),
        "glob": lambda a: ("Glob", {"pattern": a}),
        "wf": lambda a: ("WebFetch", {"url": a}),
        "ws": lambda a: ("WebSearch", {"query": a}),
        "todo": lambda a: ("TodoWrite", {"todos": []}),
        "plan": lambda a: ("ExitPlanMode", {}),
        "task": lambda a: ("Task", {"description": a or "delegate"}),
    }
    base = datetime.fromisoformat("2026-04-08T09:12:00+00:00")
    for si, (proj, steps) in enumerate(_demo_sessions()):
        proj_dir = os.path.join(root, _DEMO_PROJECTS[proj])
        os.makedirs(proj_dir, exist_ok=True)
        # Spread sessions ~5 days apart (so the activity span is > 32 days, no retention note),
        # each starting a little later in the day.
        t = base + timedelta(days=si * 5, hours=si % 4)
        lines = []
        for step in steps:
            kind, arg = step[0], (step[1] if len(step) > 1 else None)
            ts = t.isoformat().replace("+00:00", "Z")
            if kind == "p":
                rec = {"timestamp": ts, "type": "user", _DEMO_MARK: True,
                       "message": {"role": "user", "content": arg}}
                t += timedelta(minutes=4)
            else:
                name, inp = builders[kind](arg)
                rec = {"timestamp": ts, "type": "assistant", _DEMO_MARK: True,
                       "message": {"role": "assistant",
                                   "content": [{"type": "tool_use", "name": name, "input": inp}]}}
                t += timedelta(minutes=2)
            lines.append(json.dumps(rec))
        with open(os.path.join(proj_dir, f"sess-{si + 1:02d}.jsonl"), "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")


def render_demo(out_path, open_browser=True):
    """Build the report from fictional data and write it to out_path. Returns the report path."""
    root = tempfile.mkdtemp(prefix="ai-fluency-demo-")
    try:
        _write_demo_transcripts(root)
        corpus = parse(discover_files(root, allow_demo=True))
        result = analyze(corpus)
        cards, strength = build_action_plan(corpus, result)
        html_doc = build_html(corpus, result, cards, strength, None, None)
    finally:
        shutil.rmtree(root, ignore_errors=True)
    out_path = os.path.abspath(out_path)
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html_doc)
    print(f"  Demo report (fictional 'Sam' data — no real transcripts): {out_path}\n")
    if open_browser:
        try:
            webbrowser.open(f"file://{out_path}")
        except Exception:
            pass
    return out_path


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def main(argv=None):
    ap = argparse.ArgumentParser(description="Claude Insight v2 — AI fluency analyzer (one command, zero install).")
    ap.add_argument("path", nargs="?", help="transcript dir or .jsonl file (default: ~/.claude/projects)")
    ap.add_argument("-o", "--out", default="ai_fluency_report.html", help="HTML output path")
    ap.add_argument("--json", action="store_true", help="print raw metrics as JSON and exit")
    ap.add_argument("--no-open", action="store_true", help="don't auto-open the report in a browser")
    ap.add_argument("--archive", default=os.environ.get("CLAUDE_INSIGHT_ARCHIVE", DEFAULT_ARCHIVE_DIR),
                    metavar="DIR",
                    help="persistent archive that preserves transcripts beyond Claude Code's "
                         "30-day cleanup so history accumulates (default ~/.claude/insight-archive; "
                         "point at a Dropbox/iCloud folder to keep it across machines)")
    ap.add_argument("--no-archive", action="store_true",
                    help="don't copy this run's transcripts into the archive (still reads an existing one)")
    ap.add_argument("--evidence", metavar="PATH",
                    help="write the de-contaminated evidence bundle (JSON) for the two-model "
                         "analysis pipeline to PATH ('-' for stdout), then continue")
    ap.add_argument("--analysis", metavar="PATH",
                    help="merge an AI analysis (JSON from the Opus stage) into the report's skill map")
    ap.add_argument("--demo", action="store_true",
                    help="render the report from a fictional developer (no real data) — a shareable "
                         "sample of the design (writes sample_report.html unless -o is given)")
    args = ap.parse_args(argv)

    if args.demo:
        out = args.out if args.out != ap.get_default("out") else "sample_report.html"
        render_demo(out, open_browser=not args.no_open)
        return 0

    files = discover_files(args.path)

    # Default mode: maintain + read the persistent archive so we can analyze more than the
    # ~30 days Claude Code keeps on disk. Skipped when an explicit path is given.
    archive_info = None
    if not args.path:
        archive_dir = os.path.expanduser(args.archive)
        new = updated = 0
        if not args.no_archive:
            new, updated = archive_transcripts(files, archive_dir)
        arch_files = _filter_transcripts(glob.glob(os.path.join(archive_dir, "**", "*.jsonl"), recursive=True))
        merged = _dedupe_sessions(files + arch_files)
        archive_info = {
            "dir": args.archive, "enabled": not args.no_archive,
            "live_sessions": len(files), "archived_sessions": len(arch_files),
            "merged_sessions": len(merged), "new": new, "updated": updated,
        }
        files = merged

    if not files:
        where = args.path or "~/.claude/projects"
        print(f"No Claude Code transcripts found in {where}.\n"
              f"Point at your transcripts with:  python3 insight.py /path/to/dir", file=sys.stderr)
        return 1

    corpus = parse(files)
    if not corpus.real_prompts:
        print("Found transcripts but no real human-typed prompts to analyze.", file=sys.stderr)
        return 1

    result = analyze(corpus)
    cards, strength = build_action_plan(corpus, result)

    if args.evidence:
        bundle = build_evidence(corpus, result, cards, archive_info)
        text = json.dumps(bundle, indent=2)
        if args.evidence == "-":
            print(text)
        else:
            ep = os.path.abspath(args.evidence)
            os.makedirs(os.path.dirname(ep) or ".", exist_ok=True)
            with open(ep, "w", encoding="utf-8") as f:
                f.write(text)
            print(f"  Evidence: {ep}", file=sys.stderr)

    analysis = None
    if args.analysis:
        try:
            with open(os.path.expanduser(args.analysis), encoding="utf-8") as f:
                analysis = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            print(f"Could not read --analysis {args.analysis}: {e}", file=sys.stderr)
            return 1

    if args.json:
        payload = {
            "overall": result["overall"], "overall_raw": result["overall_raw"],
            "band": result["band"], "archetype": result["archetype"]["label"],
            "dimensions_raw": result["raw"], "dimensions_adjusted": result["shrunk"],
            "confidence": result["conf"], "detail": result["detail"],
            "data_ingested": {
                "files": corpus.files, "projects": len(corpus.projects),
                "bytes": corpus.total_bytes, "user_records": corpus.user_records,
                "real_prompts": len(corpus.real_prompts), "filtered": dict(corpus.filtered),
                "active_hours": round(corpus.active_seconds / 3600, 1),
                "prompt_distribution": result["dist"],
                "archive": archive_info,
            },
        }
        print(json.dumps(payload, indent=2))
        return 0

    # Render fully before touching the file, so a render error can't leave a 0-byte report.
    html_doc = build_html(corpus, result, cards, strength, archive_info, analysis)
    out_path = os.path.abspath(args.out)
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html_doc)

    print(terminal_summary(corpus, result))
    if archive_info and archive_info["enabled"]:
        print(f"  Archive: {archive_info['merged_sessions']} sessions preserved at "
              f"{archive_info['dir']} ({archive_info['new']} new, {archive_info['updated']} updated this run).")
    print(f"  Report: {out_path}\n")
    if not args.no_open:
        try:
            webbrowser.open(f"file://{out_path}")
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
