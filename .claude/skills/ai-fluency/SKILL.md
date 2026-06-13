---
name: ai-fluency
description: Analyze how the developer collaborates with Claude Code and produce an "AI fluency" builder profile — archetype, strengths, growth edges, and personalized recommendations. Use when the user asks to analyze their Claude Code usage, AI fluency, builder profile, prompting style, or "how do I use Claude / AI", or runs /ai-fluency.
argument-hint: "[--dir PATH | --mock]"
allowed-tools: Bash(python3 *), Read, Write
---

# AI Fluency Analysis

You are profiling how this developer collaborates with AI coding tools, using
their real Claude Code session transcripts. Claude Insight computes the
**numbers** deterministically; **you** provide the qualitative judgement.

## Step 1 — Collect the data

Run the collector. It prints JSON with aggregate `metrics` and a `sample_prompts`
array (the developer's actual prompts). Pass through any argument the user gave
(e.g. `--dir ~/.claude/projects`, or `--mock` to demo with fake data):

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/collect.py $ARGUMENTS
```

If it prints an error about no transcripts found, tell the user to point at
their transcript directory with `--dir PATH` (default search paths are
`~/.claude/projects` and `~/.claude/sessions`), or try `--mock` for a demo.

## Step 2 — Analyze

Read the JSON. Ground every claim in the data — cite specific numbers from
`metrics` and quote or paraphrase real prompts from `sample_prompts`. Treat the
heuristic `archetype` and `archetype_scores` as hints, not gospel: form your own
judgement from the prompts.

Determine, in order:

1. **Builder archetype** — pick exactly one and justify it from the prompts:
   - 🏗️ **Architect** — plans/designs before building
   - ⚡ **Sprinter** — high velocity, direct action, rapid iteration
   - 🐛 **Debugger** — methodical problem-solving and error-hunting
   - 🤝 **Collaborator** — seeks alignment, asks for opinions/reviews
   - 🤖 **Autonomous Agent** — delegates end-to-end workflows
2. **The five dimensions** (`steering`, `execution`, `engineering`, `product`,
   `planning` in `metrics`) — interpret each score in plain language; note the
   standout strength and the weakest dimension.
3. **AI fluency read** — how effectively do they steer the model? Look at prompt
   length/specificity, whether prompts include file paths and concrete
   constraints, how they iterate, and tool diversity.
4. **3 specific, actionable recommendations** — tied to what you actually saw in
   their prompts, not generic advice. Each should name the behavior to change
   and what to do instead.

## Step 3 — Present

Give a concise profile: archetype (with one-line reasoning), a 2–3 sentence
summary, the dimension read, and the 3 recommendations. Lead with the headline
(their archetype and the single most useful thing to improve).

If the user wants a shareable artifact, offer to generate the HTML report:

```bash
python3 -m claude_insight $ARGUMENTS --no-ai --report report.html
```

## Notes

- All of this runs locally. Transcripts are read on this machine and analyzed by
  you (Claude Code) — nothing is uploaded and no API key is involved.
- The standalone tool also has an offline mode that uses a local Ollama model
  instead of you; this skill is the path for when the user is already in Claude
  Code. See the repo README.
