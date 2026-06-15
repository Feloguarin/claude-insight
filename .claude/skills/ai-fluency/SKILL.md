---
name: ai-fluency
description: Analyze how the developer collaborates with Claude Code and produce an "AI fluency" skill map — overall score, archetype, the four AI-fluency competencies (Delegation, Description, Discernment, Diligence), the five measured dimensions, and clear what/where/how direction. Use when the user asks to analyze their Claude Code usage, AI fluency, builder profile, prompting style, or "how do I use Claude / AI", or runs /ai-fluency.
argument-hint: "[PATH | --no-open]"
allowed-tools: Bash(python3 *), Read, Write, Workflow
---

# AI Fluency Analysis — one command, full run

You produce a reliable AI-fluency **skill map** for this developer from their real
Claude Code transcripts. One run, three parts:

1. **Measure (deterministic).** `insight.py` parses transcripts, de-contaminates and
   scrubs them, and computes the numbers — rate-based, confidence-hedged, archive-backed
   so it sees **more than Claude Code's 30-day window**.
2. **Explore (Sonnet 4.6).** Parallel explorers read the evidence, one per AI-fluency competency.
3. **Analyze (Opus 4.8).** A senior assessor writes the skill map, **grounded in the bundled
   AI Fluency framework**, then verifies it is evidence-grounded.

The skill is self-contained: the engine and the framework are bundled next to this file at
`~/.claude/skills/ai-fluency/`, and all working files land in `~/.claude/insight/`.

## Step 1 — Measure + emit evidence

```bash
python3 ~/.claude/skills/ai-fluency/insight.py --evidence ~/.claude/insight/evidence.json --no-open -o ~/.claude/insight/ai_fluency_report.html $ARGUMENTS
```

This writes a deterministic report and the de-contaminated evidence bundle. If it reports
no transcripts, tell the user to pass their transcript directory as `$ARGUMENTS` (default
`~/.claude/projects`).

## Step 2 — Run the two-model analysis workflow

Print the absolute paths the workflow needs (it reads them with its own Read tool):

```bash
python3 -c "import os; print(os.path.expanduser('~/.claude/insight/evidence.json')); print(os.path.expanduser('~/.claude/skills/ai-fluency/reference/ai-fluency-framework.md'))"
```

Then call the **Workflow** tool with:
- `name`: `ai-fluency`
- `args`: `{ "evidence": "<first line above>", "framework": "<second line above>" }`

The workflow returns the analysis as a JSON object (overall_read, skill_map of the four
competencies, top_growth, strengths). **Sonnet 4.6** explores, **Opus 4.8** analyzes +
verifies — model selection is baked into the workflow.

## Step 3 — Render the final report

Write the workflow's returned JSON to `~/.claude/insight/analysis.json` (use the absolute
path — that directory already exists from Step 1), then merge it into the report:

```bash
python3 ~/.claude/skills/ai-fluency/insight.py --analysis ~/.claude/insight/analysis.json -o ~/.claude/insight/ai_fluency_report.html $ARGUMENTS
```

The report opens in the browser and now carries the Opus-authored, framework-grounded skill
map on top of the deterministic numbers. Point the user to
`~/.claude/insight/ai_fluency_report.html`.

## Step 4 — Narrate (don't re-derive)

In chat, give a short, encouraging read: the **overall score + band + archetype** in one
sentence, the **single highest-leverage growth move** grounded in one of their real prompts,
and their **strongest competency** as the foundation. Keep it to a paragraph or two; the
report has the depth.

## Fallbacks

- **No Workflow capability available?** Step 1 + a plain narration still work — the
  deterministic report is complete on its own. Skip steps 2–3.
- **Explicit path given?** Pass it as `$ARGUMENTS` in steps 1 and 3 (archiving is skipped
  for explicit paths by design).

## Notes

- Original transcripts are never modified. They're copied into an archive
  (`~/.claude/insight-archive`) so history outlives Claude Code's 30-day cleanup.
- Scores measure observable behavior, not intent; thin signals are flagged "low data" and
  hedged — don't over-claim on those.
