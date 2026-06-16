# Claude Insight

A one-command analysis of how you build with AI. It reads your Claude Code sessions and gives you a fluency score, your builder archetype, a 4-competency skill map, and exactly what to do next — all in one self-contained HTML report.

## ⚡ Install in 5 seconds

```bash
curl -fsSL https://raw.githubusercontent.com/Feloguarin/claude-insight/main/install.sh | bash
```

This installs the **`/ai-fluency`** skill into Claude Code. Then, inside Claude Code (any
folder), just run:

```text
/ai-fluency
```

That's the whole thing. You get the full run — your fluency score, builder archetype, the
AI-written 4-competency skill map, and your highest-leverage moves — in one self-contained
report (`~/.claude/insight/ai_fluency_report.html`). Requires Python 3.8+ and Claude Code;
never modifies your original transcripts.

## 🚀 What It Does

Claude Insight parses your local Claude Code transcripts and generates:

- **Overall AI Fluency Score (0–100)** — with a calibrated band (Operator → Expert) and what it means
- **Builder Archetype** — Architect, Sprinter, Debugger, Collaborator, or Autonomous Agent — chosen from *your* behavior, not keywords
- **A 4-competency skill map** — **Delegation · Description · Discernment · Diligence** (the AI Fluency framework), each placed on a 1–5 level rubric with your next move
- **Five measured dimensions** — Direction, Verification, Context-setting, Iteration, Toolcraft, each a defensible rate
- **What / Where / How** — your top growth levers, each with real evidence from your transcripts and a copy-paste prompt rewrite
- **Full data-ingested transparency** — how many real prompts (vs. tool-output/subagent/injected noise), projects, MB, and active time it's based on — across **more than the 30 days Claude Code keeps on disk** (see below)

> **Accuracy first.** Every score is a *rate* over your **real, de-contaminated prompts** pushed through a saturating curve — so using Claude *more* can never raise your score, only using it *better* can. Tool-results, subagent turns, slash-command stubs, injected system text and pasted walls of text are filtered out before anything is scored, and idle time is excluded from "active hours." Thin signals are flagged "low data" and hedged toward neutral instead of faking confidence.

## One command, the full analysis

`/ai-fluency` inside Claude Code runs the complete pass in one go:

1. **Measure** — `insight.py` de-contaminates and **scrubs** your transcripts and computes every score (rate-based, confidence-hedged, archive-backed so it sees **more than Claude Code's 30-day window**).
2. **Explore — Sonnet 4.6** — four parallel explorers, one per AI-fluency competency, read your evidence.
3. **Analyze — Opus 4.8** — a senior assessor writes the skill map grounded in [`reference/ai-fluency-framework.md`](reference/ai-fluency-framework.md), then a verifier checks every claim against your evidence and repairs it if not.

You get **one report**: your score and band, your builder archetype, the **Delegation · Description · Discernment · Diligence** skill map, and your highest-leverage moves — what to change, each with real evidence and a copy-paste prompt rewrite. The numbers are always computed deterministically; the models add judgement and direction on top, and never change the math. It runs on your existing Claude Code session — **no separate API key**; models are pinned per stage in [`.claude/workflows/ai-fluency.js`](.claude/workflows/ai-fluency.js).

Not inside Claude Code? `python3 insight.py` produces the complete deterministic report — the same scores, archetype, and skill levels — with generic (clearly-labeled) growth examples. The *personalized* growth rewrites come from the Opus stage, which runs via `/ai-fluency` inside Claude Code.

## 🧩 Use it as a Claude Code skill

This repo ships a Claude Code skill at `.claude/skills/ai-fluency/`. Inside
Claude Code, just run:

```
/ai-fluency
```

The skill runs the three-stage pipeline as **one pass that ends in a single finished
report** (it doesn't flash a score first and a report later). Opus writes your "how to
grow" cards directly from *your own* prompts — tailored before/after rewrites, not stock
examples — and the engine refuses to merge any analysis that doesn't fingerprint-match
this exact run, so one person's verdict can never leak into another's report. It's also
auto-discovered when you ask Claude Code to "analyze my AI fluency" or "profile how I use
Claude Code". If the Workflow capability isn't available, it falls back to the
deterministic report and says so plainly.

### Data export & flags

```bash
python3 insight.py --json                 # metrics + data-ingested breakdown as JSON
python3 insight.py --evidence ev.json     # write the de-contaminated evidence bundle (carries a run_fingerprint)
python3 insight.py --analysis an.json --analysis-evidence ev.json  # merge an Opus analysis, bound to this run
python3 insight.py /path/to/transcripts   # analyze a specific directory
python3 insight.py --no-open              # don't auto-open the browser
python3 insight.py --quiet               # suppress the terminal summary (used by the skill's measure step)
python3 insight.py --archive ~/my-archive # keep history in a PRIVATE, per-person durable folder
python3 insight.py --no-archive          # analyze without copying anything new
```

## ⏳ Analyzing more than 30 days

By default Claude Code **deletes transcripts older than its `cleanupPeriodDays`
setting (default `30`)**, so only ~30 days of history is ever on disk — that's a
limit of the *data*, not of this tool, which reads everything available.

Two things make Claude Insight see more:

1. **Stop the deletion.** Add this to `~/.claude/settings.json` so Claude Code
   keeps a full year (set it before more data ages out):
   ```json
   { "cleanupPeriodDays": 365 }
   ```
2. **The built-in archive (automatic).** On every run, Claude Insight copies your
   transcripts into a persistent archive (`~/.claude/insight-archive` by default)
   *before* the cleanup can remove them, then analyzes **live + archive** deduped.
   So from your first run onward your history **accumulates indefinitely**, even
   past 30 days. It only ever grows files, copies atomically, and stays 100% on
   your machine. Point it at any durable path **that is private to you** — keep it
   per-person, because a single archive folder shared between people (e.g. a synced
   team Dropbox) would merge everyone's transcripts into one report:
   ```bash
   python3 insight.py --archive ~/Dropbox/claude-archive   # survives reinstalls (your own, private folder)
   # or set CLAUDE_INSIGHT_ARCHIVE in your shell. Use --no-archive to skip a run.
   ```

## 📦 Run from source (no install)

`insight.py` is a single pure-standard-library file — clone and run it, nothing to
`pip install`:

```bash
git clone https://github.com/Feloguarin/claude-insight.git
cd claude-insight
python3 insight.py                 # analyze ~/.claude/projects, write + open the report
```

> Requires Python 3.8+. The full AI-personalized report (Sonnet explore → Opus analyze)
> comes from running `/ai-fluency` inside Claude Code; `python3 insight.py` on its own
> produces the complete deterministic report (scores, archetype, dimensions, growth levers).

## 📊 Example Output

```
  AI Fluency Score: 78/100  (Advanced)
  Archetype: 🤖 Autonomous Agent
  Based on 156 real prompts across 16 projects, 156 sessions (53.8 MB).
  Archive: 156 sessions preserved at ~/.claude/insight-archive (0 new, 1 updated this run).
  Report: ai_fluency_report.html
```

The HTML report adds the headline ring, the four-competency skill map (with your
level and next move for each), the five dimensions, your top growth levers with
before/after prompt rewrites, archetype affinity, and a full methodology appendix.

## 🏗️ Architecture

```
insight.py                       # the whole engine: parse → de-contaminate → score → report
                                 #   (pure stdlib, zero install; --evidence / --analysis hooks)
reference/
└── ai-fluency-framework.md      # the 4D framework the Opus analysis stage is grounded in
.claude/
├── skills/ai-fluency/SKILL.md   # /ai-fluency — orchestrates the one-command pipeline
└── workflows/ai-fluency.js      # Sonnet 4.6 explore → Opus 4.8 analyze → verify
tests/                           # stdlib unittest (de-contamination, scoring, archive,
                                 #   analysis-provenance, personalized growth)
```

## 📈 Metrics Computed

### The four AI-fluency competencies (skill map)
Adapted from Anthropic's *AI Fluency: Frameworks & Foundations* (the 4 Ds):
1. **Delegation** — deciding what to hand to the agent, and how to split the work
2. **Description** — telling the agent what you want (goal + constraint + acceptance test)
3. **Discernment** — evaluating what comes back (verify, ground edits, correct precisely)
4. **Diligence** — being responsible: verify before it ships, tear down, own the result

### Five measured dimensions (the signals behind the map)
1. **Direction / Briefing** — how concretely you frame requests (constraint / artifact / intent rates)
2. **Verification** — running tests / build / app after edit-bursts
3. **Context-setting** — grounding edits in a prior read (not blind edits)
4. **Iteration** — correcting precisely instead of vague rejection
5. **Toolcraft** — reaching for a healthy range of tools, not forcing one

### Archetypes (chosen from *your* behavior, not keywords)
- **🤖 Autonomous Agent** — delegates whole, end-to-end jobs and trusts the agent to run them
- **🏗️ Architect** — plans and explores before building; reads and designs first
- **🐛 Debugger** — methodical problem-solving: read to diagnose, change, verify, repeat
- **🤝 Collaborator** — works with the agent like a teammate: asks for options, gives feedback
- **⚡ Sprinter** — fast and direct, terse prompts, low ceremony; verification is the growth edge

The archetype is the nearest match to your **agency-weighted** behavior vector — it counts what *you* do (briefing, correcting, tool choice, delegation) and discounts the read-before-edit and run-the-tests habits Claude does on its own, so it reflects you, not the agent. Near-ties are reported as a blend, never a coin-flip.

## 🔧 Development

```bash
# Run the test suite (standard library only — no pytest required)
python -m unittest discover -s tests
```

## 📄 License

MIT License — see [LICENSE](LICENSE) file.


