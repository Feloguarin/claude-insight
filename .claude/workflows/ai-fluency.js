export const meta = {
  name: 'ai-fluency',
  description: 'Two-model AI-fluency analysis: Sonnet 4.6 explores the evidence, Opus 4.8 writes a skill map grounded in the AI Fluency framework, then verifies it is evidence-grounded.',
  whenToUse: 'Run by the /ai-fluency skill after insight.py emits .insight/evidence.json. Args: {evidence, framework} absolute paths.',
  phases: [
    { title: 'Explore', detail: 'Sonnet 4.6 — one explorer per 4D competency' },
    { title: 'Analyze', detail: 'Opus 4.8 — skill map grounded in the framework' },
    { title: 'Verify', detail: 'check the map is grounded; repair if not' },
  ],
}

// Resolve inputs. The /ai-fluency skill passes absolute paths via args, but if they don't
// arrive the defaults MUST still point at the exact files the skill writes — never a bare
// relative path like '.insight/evidence.json', which an agent will "helpfully" resolve to a
// stale copy elsewhere on disk (e.g. an old ~/Dropbox/.../.insight/evidence.json), silently
// analyzing the wrong dataset.
const EV = (args && args.evidence) || '~/.claude/insight/evidence.json'
const FW = (args && args.framework) || '~/.claude/skills/ai-fluency/reference/ai-fluency-framework.md'

const COMPETENCIES = [
  { key: 'Delegation',  focus: 'What they hand to the agent vs keep, and how they split work: end-to-end hand-offs vs micro-stepping, sub-agents / background jobs / planning, tool breadth (platform & path awareness). Signals: delegation_events, tool_usage, scope of prompts.' },
  { key: 'Description',  focus: 'How concretely they brief the agent: do action prompts name a file/error (artifact), carry a constraint, and state a why/acceptance test? Terse offloading vs front-loaded specific briefs. Signals: Direction detail (constraint/artifact/intent rates) + sample prompts.' },
  { key: 'Discernment',  focus: 'How they evaluate outputs: verification after edit-bursts (tests/build/run), grounding edits in a prior read, correcting precisely (symptom + rule) vs vague rejection. Signals: Verification, Context, Iteration detail. NOTE agency: verification/grounding are partly Claude-driven — credit the USER moderately.' },
  { key: 'Diligence',    focus: 'Responsibility: verifying before things go live, tearing down what was spun up, owning the result rather than blind-shipping. In a coding transcript this overlaps Discernment — weight the responsibility angle. Signals: verification teardown bonus, grounded edits.' },
]

const FINDING = {
  type: 'object', additionalProperties: false,
  required: ['competency', 'level_estimate', 'confidence', 'strengths', 'gaps', 'evidence_quotes'],
  properties: {
    competency: { type: 'string' },
    level_estimate: { type: 'integer', minimum: 1, maximum: 5 },
    confidence: { type: 'string', enum: ['low', 'medium', 'high'] },
    strengths: { type: 'array', items: { type: 'string' } },
    gaps: { type: 'array', items: { type: 'string' } },
    evidence_quotes: { type: 'array', items: { type: 'string' }, description: 'real quotes/observations from the evidence' },
    notes: { type: 'string' },
  },
}

const SKILL_ENTRY = {
  type: 'object', additionalProperties: false,
  required: ['competency', 'level', 'level_label', 'summary', 'evidence', 'next_move'],
  properties: {
    competency: { type: 'string', enum: ['Delegation', 'Description', 'Discernment', 'Diligence'] },
    level: { type: 'integer', minimum: 1, maximum: 5 },
    level_label: { type: 'string', enum: ['Emerging', 'Developing', 'Proficient', 'Advanced', 'Expert'] },
    summary: { type: 'string' },
    evidence: { type: 'array', items: { type: 'string' }, minItems: 1 },
    next_move: { type: 'string' },
  },
}

const ANALYSIS = {
  type: 'object', additionalProperties: false,
  required: ['overall_read', 'skill_map', 'top_growth', 'strengths'],
  properties: {
    overall_read: { type: 'string' },
    skill_map: { type: 'array', items: SKILL_ENTRY, minItems: 4, maxItems: 4 },
    top_growth: {
      type: 'array',
      items: {
        type: 'object', additionalProperties: false,
        required: ['title', 'why', 'how', 'example_before', 'example_after'],
        properties: {
          title: { type: 'string' }, why: { type: 'string' }, how: { type: 'string' },
          example_before: { type: 'string' }, example_after: { type: 'string' },
        },
      },
    },
    strengths: { type: 'array', items: { type: 'string' } },
  },
}

const VERDICT = {
  type: 'object', additionalProperties: false,
  required: ['is_grounded', 'ungrounded_claims'],
  properties: {
    is_grounded: { type: 'boolean' },
    ungrounded_claims: { type: 'array', items: { type: 'string' } },
    notes: { type: 'string' },
  },
}

const READ = `Using your Read tool, read EXACTLY these two files and use ONLY them — the framework at ` +
  `${FW} and the de-contaminated evidence bundle (JSON) at ${EV}. ` +
  `Do NOT search for, guess at, or substitute any other path: there may be stale evidence.json ` +
  `copies elsewhere on disk (old archives, Dropbox/iCloud folders) — ignore them entirely. If either ` +
  `file is missing, STOP and report that rather than reading a different one. (A leading ~ means your ` +
  `home directory — expand it to $HOME before reading.) The evidence is real and ` +
  `local; ground everything in it — quote real prompts; never invent.`

// ---- Explore: Sonnet 4.6, one thorough explorer per competency -------------
phase('Explore')
const findings = await parallel(COMPETENCIES.map(c => () =>
  agent(
    `${READ}\n\nYou are a careful, thorough analyst exploring ONE AI-fluency competency: ` +
    `**${c.key}**.\n${c.focus}\n\nUse the level rubric in the framework. Estimate a level (1–5), ` +
    `set confidence from how much evidence exists (hedge thin signals), and list concrete strengths, ` +
    `gaps, and real evidence quotes. Be specific to THIS person.`,
    { label: `explore:${c.key}`, phase: 'Explore', model: 'sonnet', schema: FINDING }
  ).then(f => f && { ...f, competency: f.competency || c.key })
)).then(r => r.filter(Boolean))

log(`Explored ${findings.length}/4 competencies (Sonnet 4.6)`)

// ---- Analyze: Opus 4.8 writes the grounded skill map -----------------------
phase('Analyze')
const findingsJson = JSON.stringify(findings, null, 2)
const analystPrompt =
  `${READ}\n\nYou are the senior AI-fluency assessor (write like a kind, exacting teacher). ` +
  `Four Sonnet explorers produced these competency findings:\n\n${findingsJson}\n\n` +
  `Reconcile them with the deterministic scores in the evidence bundle and the framework's level ` +
  `rubric and "what good looks like". Produce the final assessment per the framework's OUTPUT CONTRACT: ` +
  `an overall_read, a skill_map with EXACTLY the four competencies (Delegation, Description, Discernment, ` +
  `Diligence) in that order, top_growth, and strengths. ` +
  `\n\nThe top_growth section is the heart of the report — it is rendered as this person's "how to grow" ` +
  `cards, so it MUST be fully custom, never generic advice. Produce 3 items (2 only if the data is thin). ` +
  `For each: a sharp, specific title; a "why" that cites THIS person's own numbers/pattern (e.g. their ` +
  `constraint rate, a habit you saw); a concrete "how"; and the before/after where example_before is a ` +
  `REAL prompt they actually wrote (copy it VERBATIM from the evidence's sample_prompts or weak_examples — ` +
  `do not invent or paraphrase it) and example_after is your tailored rewrite of THAT exact prompt, ready ` +
  `to paste, fixing the specific gap. Pack it with signal: name their files, tools, projects, and phrasing. ` +
  `If two growth items would share the same before/after, replace one so no example repeats. ` +
  `Respect agency (discount Claude-driven habits) and confidence (hedge thin signals). ` +
  `Every claim — and every example_before — must be grounded in the evidence.`
let analysis = await agent(analystPrompt, { label: 'analyze', phase: 'Analyze', model: 'opus', schema: ANALYSIS })

// ---- Verify: is the map actually grounded? repair once if not --------------
phase('Verify')
const verdict = await agent(
  `${READ}\n\nAdversarially check this AI-fluency skill map against the evidence. Flag any claim that ` +
  `is generic, ungrounded, inflated, or ignores low confidence. Default to is_grounded=false if unsure.\n\n` +
  `SKILL MAP:\n${JSON.stringify(analysis, null, 2)}`,
  { label: 'verify', phase: 'Verify', model: 'opus', schema: VERDICT }
)
if (verdict && verdict.is_grounded === false && (verdict.ungrounded_claims || []).length) {
  log(`Repairing ${verdict.ungrounded_claims.length} ungrounded claim(s)`)
  analysis = await agent(
    `${READ}\n\nRevise this skill map to fix these grounding problems — replace generic/inflated/ungrounded ` +
    `statements with evidence-grounded, appropriately-hedged ones. Keep the same JSON shape.\n\n` +
    `PROBLEMS:\n${(verdict.ungrounded_claims || []).map((c, i) => `${i + 1}. ${c}`).join('\n')}\n\n` +
    `CURRENT:\n${JSON.stringify(analysis, null, 2)}`,
    { label: 'repair', phase: 'Verify', model: 'opus', schema: ANALYSIS }
  )
}

return analysis
