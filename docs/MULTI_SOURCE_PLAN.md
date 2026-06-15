# Multi-Source AI-Fluency Analysis — Implementation Work Order

**Audience:** a fresh Claude Code agent implementing this. You have no prior context —
everything you need is here. Read `insight.py` once before starting.

**Goal:** let `claude-insight` analyze AI-fluency from **four** coding-agent sources, not
just Claude Code, by adding pluggable **source adapters** that normalize each tool's local
logs into the data shape the existing scorers already consume. Scoring, the evidence bundle,
the archive, and the Sonnet→Opus 4D analysis pipeline stay **unchanged**.

**In scope (this work order):**
1. `claude-code` — `~/.claude/projects/**/*.jsonl` (already supported; becomes an adapter)
2. `claude-desktop` — desktop "Cowork"/agent sessions, `audit.jsonl`
3. `codex` — OpenAI Codex CLI, `~/.codex/sessions/**/rollout-*.jsonl`
4. `cursor` — Cursor IDE, SQLite `state.vscdb`

**Out of scope (explicitly deferred):** Claude.ai **chat** export (`conversations.json`).
Pure chat lacks edit/verify/tool signals, so three of the four competencies aren't
measurable; we are not building it now. Do not add it.

**Hard constraints (do not violate):**
- **Single file, standard library only.** Everything lives in `insight.py`. Allowed stdlib:
  `json`, `sqlite3`, `glob`, `re`, `os`, etc. **No third-party deps. No new files** except
  tests + fixtures. (Keeps the zero-install `curl … | python3 -` path working.)
- **Read-only, local, private.** Never write to or mutate any source's files (the Claude
  desktop `audit.jsonl` carries an `_audit_hmac` integrity field — touching it can invalidate
  sessions). Nothing personal enters git: the report, `.insight/`, and the archive stay
  git-ignored.
- **No regressions.** The existing 65 tests must stay green with **zero changes** to their
  assertions. The `claude-code` path must produce byte-identical results after the refactor.
- **Honesty over coverage.** If a source can't observe a signal, mark the competency
  *not measurable* — never fake or impute it (see §7).

---

## 1. The architecture you're extending

`insight.py` is a pipeline. Today only the first stage is Claude-Code-specific:

```
discover_files() → parse() → Corpus → analyze()/score_*() → build_evidence() / build_html()
                   ^^^^^^^^^^^^^^^^^^                          → (workflow) Sonnet explore → Opus 4D map
                   tool-specific                shared, tool-agnostic — DO NOT TOUCH the scorers
```

**The contract the scorers depend on** (study these in `insight.py` before coding):

- `Corpus` (class ~line 290): the parsed result. Relevant fields an adapter must populate:
  - `real_prompts` — list of `{"text", "project", "session", "idx"}` (human-typed prompts only)
  - `tool_usage` — `Counter` of canonical tool name → count
  - `total_tool_calls`, `delegation_events`
  - `first_ts`, `last_ts`, `active_seconds` (gap-capped; reuse existing logic)
  - `sessions` — `session_id → {"project", "timeline":[…]}`
  - `user_records`, `filtered` (Counter of why records were dropped — for the transparency panel)
- **Per-session `timeline`** — an ordered list of events, each either:
  - `{"kind":"prompt", "text": str, "rec": <the real_prompts dict>}`
  - `{"kind":"tool", "name": <lowercase canonical>, "file": <path|None>, "cmd": <bash str|None>}`
- **The canonical tool vocabulary** the scorers branch on (do not invent new names — map onto these):

  | Canonical names | Used by | Meaning |
  |---|---|---|
  | `read`, `grep`, `glob` (`READ_TOOLS`) | `score_context` | grounding: a file was read before editing |
  | `edit`, `write`, `multiedit`, `notebookedit` (`EDIT_TOOLS`) | `score_context`, `score_verification` | a file edit; opens an "edit burst" |
  | `bash` (+ `cmd`) | `score_verification` (`VERIFY_RE`/`TEARDOWN_RE` on `cmd`) | a command run; verification/teardown if it matches |
  | `agent`, `task`, `workflow`, `enterplanmode`, `exitplanmode` (`DELEGATION_TOOLS`) | `analyze()` delegation axis | a hand-off / planning act |
  | any other name | `score_toolcraft` | counts toward tool breadth/evenness |

  So **an adapter's whole job** is: emit `real_prompts`, and emit `timeline` tool events whose
  `name` is mapped onto this vocabulary, with `file` set for edits/reads and `cmd` set for shells.
  Edits map to `edit`/`write`; shell commands map to `bash` with the command string in `cmd`;
  file reads map to `read`; everything else keeps its lowercased native name (it still counts
  toward Toolcraft breadth). Get this mapping right and every existing scorer "just works."

---

## 2. Target design: the `SourceAdapter` abstraction

Keep it single-file and lightweight. Add a small adapter layer near the top of `insight.py`
(after the constants), then make the current Claude Code logic the first adapter.

```python
# Each adapter is a small object/dict with these four capabilities:
class SourceAdapter:
    name = "claude-code"                 # CLI value for --source
    archive_enabled = True               # whether the persistent archive applies (see §8)

    @staticmethod
    def detect() -> bool:
        """True if this tool's data is present on this machine (cheap path check)."""

    @staticmethod
    def discover(explicit_path: str | None) -> list[str]:
        """Return the list of session files (or DB paths) to parse."""

    @staticmethod
    def iter_events(path: str):
        """Yield normalized events for ONE session, in order:
             {"role":"user","text":str,"ts":iso}                       # a human prompt
             {"role":"tool","name":<canonical>,"file":path|None,
              "cmd":str|None,"ts":iso,"meta":{…}}                      # an agent tool call
           Plus one {"role":"session","project":str,"session_id":str} header event.
           De-contamination (dropping harness/system/tool-output records) happens HERE,
           and each drop increments a `filtered[reason]` counter passed in."""

    capabilities = {"prompts": True, "edits": True, "verify": True,
                    "reads": True, "delegation": True}
```

Then:
- A **registry**: `ADAPTERS = {a.name: a for a in (ClaudeCodeAdapter, ClaudeDesktopAdapter, CodexAdapter, CursorAdapter)}`.
- **`parse(files, adapter)`** becomes generic: it walks `adapter.iter_events()` and builds the
  `Corpus`/`timeline` exactly as today (the active-time gap-capping, `real_prompts`, `tool_usage`,
  `delegation_events` accounting all move here, source-agnostic).
- **Auto-detect**: if `--source` is omitted, pick `claude-code` if present, else the first
  adapter whose `detect()` is true; `--source all` analyzes each available source and emits one
  report per source (do **not** merge sources into one score in v1 — see §7).

---

## 3. Phase 0 — Refactor to the adapter interface (no behavior change)

**This is the riskiest phase for regressions; do it first and prove it with the existing tests.**

1. Extract the Claude Code branch of `discover_files()` + the record-parsing loop inside
   `parse()` into `ClaudeCodeAdapter` (`discover` = today's glob + `_filter_transcripts` +
   `_dedupe_sessions`; `iter_events` = today's per-line classification, emitting the normalized
   events above). The de-contamination rules (tool-results, `isSidechain`, `isMeta`,
   `_looks_injected`, `…/subagents/…`) move into the adapter unchanged.
2. Rewrite `parse()` to be adapter-driven and build the identical `Corpus`.
3. **Acceptance:** all 65 existing tests pass **with no edits to the tests**. Add one test that
   asserts `analyze(parse(files, ClaudeCodeAdapter))` equals the pre-refactor output on a fixture
   (lock in "no behavior change").

Only once Phase 0 is green do you add new sources.

---

## 4. Phase 1 — Claude Desktop adapter (`claude-desktop`)

The closest sibling to Claude Code — do it right after the refactor.

- **Discover:** `~/Library/Application Support/Claude/local-agent-mode-sessions/<account>/<workspace>/<session>/audit.jsonl`
  (recursively glob `audit.jsonl`). Each file = one session. The sibling
  `claude-code-sessions/**/local_*.json` files are **metadata only — ignore them.** The
  `IndexedDB` LevelDB is cloud-thin (no conversation content) — **ignore it.**
- **Format:** newline-delimited JSON. `type ∈ {system, user, assistant, result, rate_limit_event}`.
  - `system` (`subtype:"init"`): `{cwd, session_id, tools[], model, permissionMode, …}` → use for
    the `session` header event (project = basename of `cwd`).
  - `user`/`assistant`: carry `message.content` with `tool_use` / `tool_result` blocks — **the
    same shape as Claude Code.** Reuse the Claude Code classification almost verbatim.
  - `result`: `{num_turns, total_cost_usd, duration_ms, permission_denials:[…]}`.
    `permission_denials` is a **list of `{tool_name, tool_use_id, tool_input}`** (not a count) —
    a strong Discernment signal (user scrutiny); surface it in `behavior` for the analysis stage.
- **Tool mapping:** identity (lowercase) — desktop uses the same names (`Bash, Read, Edit, Write,
  MultiEdit, Grep, Glob, Agent, Task, Skill, WebFetch, WebSearch, mcp__*`). `Agent`/`Task` →
  delegation. `Skill` → a delegation/toolcraft signal.
- **De-contamination:** same as Claude Code (drop `tool_result` user records, `system`/`result`
  envelopes from prompt counting, injected text).
- **Capabilities:** all four `True`.
- **Gotchas:** internal undocumented format (treat keys as optional); `_audit_hmac` present →
  **read-only, never rewrite.** (Counts on this machine for your sanity check: ~43 `audit.jsonl`
  files, ~1.8k user turns.)
- **Acceptance:** a fixture `audit.jsonl` parses to the right prompt count, edit/verify episodes,
  and delegation events; report renders; `permission_denials` appears in the evidence bundle.

---

## 5. Phase 2 — Codex CLI adapter (`codex`)

- **Discover:** `~/.codex/sessions/**/rollout-*.jsonl` **and** `~/.codex/archived_sessions/rollout-*.jsonl`.
  (Optional: `~/.codex/history.jsonl` is the cleanest raw-prompt surface — `{session_id, text, ts}`
  — usable as a fallback/cross-check, but the rollout files are authoritative for tool signals.)
- **Format:** JSONL; every line `{"type", "timestamp", "payload"}`. Outer `type ∈
  {session_meta, turn_context, response_item, event_msg, compacted}`. **Treat every payload key as
  optional** (pre-GA, drifts across versions).
- **Record → normalized-event mapping:**

  | Codex record | → normalized event |
  |---|---|
  | `response_item`, `role:user`, `content[].text` | `{"role":"user","text":…}` (a real prompt) |
  | `response_item`/`function_call`, `name:exec_command`, `arguments.{command,workdir}` | `{"role":"tool","name":"bash","cmd": <joined command>}` |
  | `event_msg`, `type:exec_command_end`, `{exit_code,status,duration}` | enrich the preceding `bash` event's `meta` (optional; not required for scoring) |
  | `custom_tool_call`, `name:apply_patch`, `input` (unified diff) | parse op+path: `Add File:`→`{"name":"write","file":…}`, `Update File:`→`{"name":"edit","file":…}`, `Delete File:`→`{"name":"edit","file":…}` |
  | `function_call`, `name:update_plan` | `{"role":"tool","name":"enterplanmode"}` (planning → delegation axis) |
  | `function_call`, `name:web_search_call` | `{"role":"tool","name":"websearch"}` (toolcraft breadth) |
  | `function_call`, `name:request_user_input` | a tool event named `request_user_input` (toolcraft); **not** a user prompt |
- **De-contamination (drop these, count in `filtered`):** `role:developer` records;
  `session_meta.base_instructions`; `turn_context.developer_instructions` and the Skills
  boilerplate prepended to `turn_context.user_instructions`; `reasoning` items
  (`encrypted_content` is opaque and the `summary[]` proved unreliable — **do not score
  reasoning**); all `exec_command` stdout/stderr/`formatted_output` (tool output, never user text).
- **Project grouping:** Codex sessions are flat per-run files (no project hierarchy). Derive
  `project` from `session_meta.git.repository_url` or `turn_context.cwd` (basename); fall back to
  the session id.
- **Capabilities:** `prompts, edits, verify, reads(partial — exec-based), delegation` all `True`;
  note reasoning depth is not a signal here.
- **Verified caveats to honor:** the optimistic "readable reasoning summaries" and
  "memory_citation" signals from research were **empirically false** — do not rely on them.
  Absolute paths in `apply_patch` diffs (`/Users/<name>/…`) must be **normalized to repo-relative**
  before they reach the evidence bundle (see §9).
- **Acceptance:** fixture rollout file → correct prompts (no developer/harness lines),
  `bash`/`edit` episodes from `exec_command`/`apply_patch`, plan events counted as delegation.

---

## 6. Phase 3 — Cursor adapter (`cursor`)

Highest effort, highest format-drift; do last. SQLite via stdlib `sqlite3`.

- **Discover:** `~/Library/Application Support/Cursor/User/globalStorage/state.vscdb` (global)
  and `~/Library/Application Support/Cursor/User/workspaceStorage/<hash>/state.vscdb` (per-workspace).
  *(macOS path; document the Linux/Windows equivalents in a comment.)*
- **Format:**
  - Global `state.vscdb` → table **`cursorDiskKV(key TEXT, value BLOB)`** (NOT VS Code's
    `ItemTable`). Key families: `composerData:<id>` (session metadata JSON), `bubbleId:<composerId>:<bubbleId>`
    (one message each), `checkpointId:<…>` (file snapshots/diffs).
  - Per-workspace `state.vscdb` → table `ItemTable`; legacy keys `aiService.prompts`,
    `aiService.generations` (older chat-panel format — fallback for pre-Composer versions).
- **Record → normalized-event mapping (per `bubbleId` JSON):**

  | Bubble field | → normalized event |
  |---|---|
  | `type == 1` (user), `text` | `{"role":"user","text":…}` |
  | `type == 2` (assistant) with `toolFormerData` | a tool event: map `toolFormerData.name` → canonical (`run_terminal_cmd`→`bash`+`cmd`; edit/apply tools→`edit`/`write`+`file`; `read_file`→`read`) |
  | `toolFormerData.userDecision` (`approved`/`rejected`) | Discernment signal → put in event `meta`; surface in `behavior` |
  | `toolFormerData.result.diff.chunks` / `checkpointId` diffs | the actual edit (file + scope) |
  | `isAgentic` (on the composer/session) | session capability flag: `False` = chat-only session (prompts only) |
- **Capabilities:** per-**session**, not per-source: agent/Composer sessions → all four `True`;
  `isAgentic == False` sessions → `{prompts:True, edits/verify/reads/delegation:False}` (chat-only;
  scored for Description only, others N/A — see §7).
- **Gotchas (verified):** **never read the live DB** — `state.vscdb` can be 12–50 GB and is WAL-mode
  while Cursor is open; **copy it to a temp file first**, open read-only (`file:…?mode=ro&immutable=1`),
  and `LIMIT` your queries. Session IDs are fragmented (composer vs store ids are disjoint) — group
  by `composerId`. Format drift is **high** with no versioning contract — probe key families
  defensively and fall back to `aiService.*`. Cloud-primacy: local SQLite is authoritative only with
  Privacy Mode on; that's fine (we only read local).
- **Acceptance:** a small fixture `state.vscdb` (build it in the test with `sqlite3`) → prompts and
  agent tool/edit events parsed; an `isAgentic:false` session is scored Description-only.

---

## 7. Capability-aware reporting (honesty)

- Bump the evidence bundle to **`claude-insight-evidence/2`**: add top-level `source` (string) and
  `capabilities` (the merged map for what was observed). Keep everything else identical so the
  workflow/`--analysis` path is backward compatible.
- In `analyze()`/`build_html()`: when a capability is `False`, the dependent dimension(s) render as
  **"not measurable from <source>"** and are **excluded from the overall score's weighting**
  (re-normalize weights over the measurable dimensions) rather than scored as 0. Add the source +
  capability summary to the "How much data this is based on" panel.
- `reference/ai-fluency-framework.md`: add a short **"signal availability per source"** appendix so
  the Opus stage hedges the same way (don't claim Diligence for a chat-only Cursor session).
- **v1 does NOT merge sources into one score.** `--source all` emits one report per available
  source. A unified cross-tool fluency view (different tools → different baselines) is a future
  item, explicitly out of scope here. Say so in the report when multiple sources exist.

---

## 8. Archive + CLI

- **Archive (`archive_transcripts`, `_dedupe_sessions`, `_filter_transcripts`):** gate on
  `adapter.archive_enabled`. `claude-code` and `claude-desktop` keep archiving (they have the
  ~30-day-style local churn). `codex` (date-foldered, not auto-deleted) and `cursor` (cumulative
  SQLite) set `archive_enabled = False` in v1 — read live, don't archive. Generalizing the archive
  to non-file sources is a later item.
- **CLI:** add `--source {claude-code,claude-desktop,codex,cursor,auto,all}` (default `auto`).
  Keep the existing positional `path` and all current flags working (when a `path` is given, use the
  adapter named by `--source`, or `claude-code` by default). `--evidence`/`--analysis` unchanged.
  `--json` gains the `source` + `capabilities` fields.

---

## 9. Privacy (unchanged posture, one new rule)

- Report, `.insight/`, archive remain git-ignored. No source content is ever committed.
- **New rule for every adapter:** normalize absolute home paths (`/Users/<name>/…`,
  `/home/<name>/…`, `C:\\Users\\<name>\\…`) to repo-relative or `~/…` before anything reaches the
  evidence bundle or report (Codex diffs and Cursor diffs both embed absolute paths). Add a
  `_normalize_path()` helper and a test that no `/Users/` leaks into `build_evidence` output.

---

## 10. Test plan & definition of done

Add fixtures + tests in `tests/` (stdlib `unittest`, matching the existing style). Per adapter:
- a tiny synthetic session fixture (Codex: a `rollout-*.jsonl`; Claude desktop: an `audit.jsonl`;
  Cursor: a `state.vscdb` built in-test with `sqlite3`),
- assert: prompt count after de-contamination, edit/verify/read events land on the canonical
  vocabulary, delegation events counted, `build_evidence`/`build_html` render, capabilities correct,
  and **no absolute path leaks**.
- Plus the Phase 0 "no behavior change for claude-code" regression test.

**Definition of done:**
- [ ] Phase 0 refactor merged; all 65 existing tests green, unmodified.
- [ ] `claude-desktop`, `codex`, `cursor` adapters parse real local data on a dev machine.
- [ ] `--source {auto,all,…}` works; capability-aware report hedges unmeasurable competencies.
- [ ] New per-adapter tests green; path-leak test green; single-file + stdlib-only preserved.
- [ ] README "Two ways to run" table updated to list supported sources; framework appendix added.
- [ ] Report/evidence/archive still git-ignored; nothing personal committed.

## 11. Risks & explicit DO-NOTs
- **DO NOT** modify the scorers (`score_*`) or the existing tests' assertions — adapt *into* them.
- **DO NOT** write to any source file (Claude desktop `_audit_hmac` integrity; everything read-only).
- **DO NOT** open Cursor's live `state.vscdb` — copy first, open read-only, `LIMIT` queries.
- **DO NOT** add dependencies or new runtime files — single file, stdlib only.
- **DO NOT** fake unmeasurable competencies — mark N/A and re-normalize weights.
- **DO NOT** score Codex `reasoning` summaries or trust `memory_citation` (verified unreliable).
- **Format drift** (Codex pre-GA, Cursor unversioned): treat all keys optional; fail soft with a
  clear message naming the source and what couldn't be parsed.
- **Build order:** Phase 0 → Claude Desktop → Codex → Cursor. Land each behind its own commit so a
  later source's churn can't regress an earlier one.
