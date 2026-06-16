"""
Tests for the v2 single-file engine (insight.py).

Focus: the accuracy guarantees that v1 violated — prompt de-contamination,
rate-based scoring that can't be inflated by volume, gap-capped active time,
and confidence shrinkage of thin signals. Pure stdlib unittest.
"""
import contextlib
import glob
import io
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import insight  # noqa: E402


def _rec(**kw):
    return json.dumps(kw)


def write_session(dirpath, name, records):
    path = os.path.join(dirpath, name)
    with open(path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(r + "\n")
    return path


def user_text(text, **extra):
    e = {"type": "user", "timestamp": extra.pop("ts", "2026-01-01T00:00:00Z"),
         "message": {"role": "user", "content": text}}
    e.update(extra)
    return _rec(**e)


def user_tool_result(ts="2026-01-01T00:00:01Z"):
    return _rec(type="user", timestamp=ts,
                message={"role": "user", "content": [{"type": "tool_result", "content": "ok"}]})


def assistant_tool(name, ts="2026-01-01T00:00:02Z", **inp):
    return _rec(type="assistant", timestamp=ts,
                message={"role": "assistant",
                         "content": [{"type": "tool_use", "name": name, "input": inp}]})


class TestDecontamination(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_filters_noise_keeps_real_prompts(self):
        recs = [
            user_text("add a login endpoint to api.py, only touch that file"),  # real
            user_tool_result(),                                                 # tool result
            user_text("<task-notification>\n<task-id>abc</task-id>"),           # injection marker
            user_text("You are a senior engineer wiring a trading bot. " + "x" * 50),  # subagent leak
            _rec(type="user", isSidechain=True, timestamp="2026-01-01T00:00:03Z",
                 message={"role": "user", "content": "subagent internal prompt"}),  # sidechain
            _rec(type="user", isMeta=True, timestamp="2026-01-01T00:00:04Z",
                 message={"role": "user", "content": "meta injected"}),             # meta
            user_text("y" * 7000),                                              # > 6KB paste
            user_text("run the tests"),                                         # real
        ]
        write_session(self.tmp, "s1.jsonl", recs)
        corpus = insight.parse(insight.discover_files(self.tmp))
        texts = [p["text"] for p in corpus.real_prompts]
        self.assertEqual(len(texts), 2)
        self.assertIn("add a login endpoint to api.py, only touch that file", texts)
        self.assertIn("run the tests", texts)
        # everything else was filtered, and the breakdown is recorded
        self.assertGreaterEqual(corpus.filtered["tool results"], 1)
        self.assertGreaterEqual(corpus.filtered["subagent turns"], 1)
        self.assertGreaterEqual(corpus.filtered["meta-injected"], 1)
        self.assertGreaterEqual(corpus.filtered["injected / pasted"], 2)


class TestNoVolumeInflation(unittest.TestCase):
    """Doing MORE of the same must not raise the score (rate-based)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_repeating_a_weak_prompt_does_not_help(self):
        few = [user_text("do it")] * 3
        many = [user_text("do it")] * 60
        write_session(self.tmp, "few.jsonl", few)
        c1 = insight.parse(insight.discover_files(self.tmp))
        d1, _, _ = insight.score_direction(c1)

        tmp2 = tempfile.mkdtemp()
        write_session(tmp2, "many.jsonl", many)
        c2 = insight.parse(insight.discover_files(tmp2))
        d2, _, _ = insight.score_direction(c2)
        # 20x the volume of the same weak prompt -> not a higher score
        self.assertLessEqual(d2, d1 + 1.0)


class TestActiveTimeCapsIdle(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_idle_gap_is_capped(self):
        recs = [
            user_text("start", ts="2026-01-01T00:00:00Z"),
            user_text("end after a week of idle", ts="2026-01-08T00:00:00Z"),
        ]
        write_session(self.tmp, "s.jsonl", recs)
        corpus = insight.parse(insight.discover_files(self.tmp))
        # one ~7-day gap must be capped at GAP_CAP_SECONDS, not counted as a week
        self.assertLessEqual(corpus.active_seconds, insight.GAP_CAP_SECONDS + 1)


class TestConfidenceShrinkage(unittest.TestCase):
    def test_thin_signal_pulled_toward_50(self):
        # a high raw score on tiny n must shrink toward 50
        shrunk, c = insight.shrink(90.0, n=3, target_n=12)
        self.assertLess(shrunk, 90.0)
        self.assertGreater(shrunk, 50.0)
        self.assertAlmostEqual(c, 0.25, places=3)
        # full data -> no shrink
        shrunk2, c2 = insight.shrink(90.0, n=60, target_n=12)
        self.assertEqual(round(shrunk2), 90)
        self.assertEqual(c2, 1.0)


class TestContextGrounding(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_blind_edit_scores_lower_than_grounded(self):
        grounded = [
            user_text("fix the bug"),
            assistant_tool("Read", file_path="/x/a.py"),
            assistant_tool("Edit", file_path="/x/a.py"),
        ]
        blind = [
            user_text("fix the bug"),
            assistant_tool("Edit", file_path="/x/a.py"),  # edited without reading
        ]
        write_session(self.tmp, "g.jsonl", grounded)
        cg = insight.parse(insight.discover_files(self.tmp))
        sg, dg, _ = insight.score_context(cg)

        tmp2 = tempfile.mkdtemp()
        write_session(tmp2, "b.jsonl", blind)
        cb = insight.parse(insight.discover_files(tmp2))
        sb, db, _ = insight.score_context(cb)
        self.assertGreater(sg, sb)
        self.assertEqual(dg["rate"], 1.0)
        self.assertEqual(db["rate"], 0.0)


class TestEndToEnd(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_full_run_and_html(self):
        recs = [
            user_text("add a /health endpoint to server.py, only that file, so the LB can probe it"),
            assistant_tool("Read", file_path="/x/server.py"),
            assistant_tool("Edit", file_path="/x/server.py"),
            assistant_tool("Bash", command="python -m pytest -q"),
            user_text("run it"),
        ]
        write_session(self.tmp, "s.jsonl", recs)
        corpus = insight.parse(insight.discover_files(self.tmp))
        result = insight.analyze(corpus)
        self.assertIn(result["band"], [b[0] for b in insight.BANDS])
        self.assertTrue(0 <= result["overall"] <= 100)
        cards, strength = insight.build_action_plan(corpus, result)
        html = insight.build_html(corpus, result, cards, strength)
        self.assertIn("AI Fluency", html)
        self.assertIn("How much data this is based on", html)
        self.assertIn(result["band"], html)


class TestEdgeCases(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_real_prompts_but_zero_tool_calls_renders(self):
        # regression: zero tool calls used to crash build_html with KeyError: 'evenness'
        write_session(self.tmp, "chat.jsonl", [user_text("hi"), user_text("what can you do?")])
        rc = insight.main([self.tmp, "-o", os.path.join(self.tmp, "r.html"), "--no-open"])
        self.assertEqual(rc, 0)
        html = open(os.path.join(self.tmp, "r.html"), encoding="utf-8").read()
        self.assertIn("AI Fluency", html)
        self.assertNotIn("{", html.split("<style>")[0])  # no template leaks before CSS

    def test_self_authored_file_edit_is_grounded(self):
        # regression: editing a file the agent WROTE this session must count as grounded
        recs = [
            user_text("make a config"),
            assistant_tool("Write", file_path="/x/conf.py"),
            assistant_tool("Edit", file_path="/x/conf.py"),   # never Read — but we wrote it
            assistant_tool("Edit", file_path="/x/conf.py"),
        ]
        write_session(self.tmp, "s.jsonl", recs)
        corpus = insight.parse(insight.discover_files(self.tmp))
        _, detail, blind = insight.score_context(corpus)
        self.assertEqual(detail["rate"], 1.0)
        self.assertEqual(blind, [])

    def test_injected_head_allows_casual_youre(self):
        self.assertFalse(insight._looks_injected("you're right, fix the login bug in auth.py"))
        self.assertTrue(insight._looks_injected("You are a senior engineer. Your task is ..."))

    def test_archetype_reflects_user_not_claude(self):
        # A heavy delegator with terse prompts must read as the Autonomous Agent even when
        # Claude's read-before-edit / verify habits are maxed — those Claude-driven
        # dimensions are agency-discounted.
        dims = {"Direction": 48, "Verification": 100, "Context": 100, "Iteration": 62, "Toolcraft": 84}
        a = insight.classify_archetype(dims, delegation_score=100)
        self.assertEqual(a["primary"], "Autonomous Agent")
        # the same profile with NO delegation should NOT read as the Autonomous Agent
        b = insight.classify_archetype(dims, delegation_score=0)
        self.assertNotEqual(b["primary"], "Autonomous Agent")


class TestArchive(unittest.TestCase):
    """The archive is what lets analysis exceed Claude Code's 30-day on-disk retention."""

    def setUp(self):
        self.live = tempfile.mkdtemp()
        self.arch = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.live, "proj"), exist_ok=True)
        self.f = write_session(os.path.join(self.live, "proj"), "sess.jsonl",
                               [user_text("first prompt")])

    def test_copies_new_then_skips_unchanged_then_updates_on_growth(self):
        new, updated = insight.archive_transcripts([self.f], self.arch)
        self.assertEqual((new, updated), (1, 0))
        dest = os.path.join(self.arch, "proj", "sess.jsonl")
        self.assertTrue(os.path.exists(dest))
        # second run, unchanged -> no copy
        new, updated = insight.archive_transcripts([self.f], self.arch)
        self.assertEqual((new, updated), (0, 0))
        # the live file grows (a new turn) -> archive copy is refreshed
        with open(self.f, "a", encoding="utf-8") as fh:
            fh.write(user_text("second prompt") + "\n")
        new, updated = insight.archive_transcripts([self.f], self.arch)
        self.assertEqual((new, updated), (0, 1))
        self.assertEqual(os.path.getsize(dest), os.path.getsize(self.f))

    def test_archive_never_truncates_on_smaller_live(self):
        # if a fresh (smaller) file ever shadows an older richer archive copy, we keep the big one
        insight.archive_transcripts([self.f], self.arch)
        dest = os.path.join(self.arch, "proj", "sess.jsonl")
        big = os.path.getsize(dest)
        # archive holds the full history; a truncated live copy must NOT shrink it via dedupe
        merged = insight._dedupe_sessions([self.f, dest])
        self.assertEqual(len(merged), 1)

    def test_dedupe_prefers_largest_and_keeps_distinct_sessions(self):
        # same session in two roots, different sizes -> the larger (more complete) wins
        d2 = os.path.join(self.arch, "proj")
        os.makedirs(d2, exist_ok=True)
        small = write_session(d2, "sess.jsonl", [user_text("x")])  # smaller copy of same session
        merged = insight._dedupe_sessions([self.f, small])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0], self.f)  # the bigger live file, not the small archive copy
        # a genuinely different session is preserved
        other = write_session(os.path.join(self.live, "proj"), "other.jsonl", [user_text("y")])
        merged2 = insight._dedupe_sessions([self.f, small, other])
        self.assertEqual(len(merged2), 2)

    def test_main_merges_archive_so_old_sessions_still_count(self):
        # An "old" session that exists ONLY in the archive (Claude Code already deleted the live
        # copy) must still be analyzed. Live dir is empty; the archive supplies the history.
        empty_live = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.arch, "oldproj"), exist_ok=True)
        write_session(os.path.join(self.arch, "oldproj"), "old.jsonl",
                      [user_text("add a /health endpoint to server.py, only that file, so the LB can probe it"),
                       user_text("now run the tests to confirm it works")])
        out = os.path.join(empty_live, "r.html")
        os.environ["CLAUDE_PROJECTS_DIR"] = empty_live  # discover_files reads the empty live dir
        try:
            # no positional path -> archive logic engages; --archive supplies the old session
            rc = insight.main(["--archive", self.arch, "-o", out, "--no-open"])
        finally:
            del os.environ["CLAUDE_PROJECTS_DIR"]
        self.assertEqual(rc, 0)
        with open(out, encoding="utf-8") as fh:
            html = fh.read()
        self.assertIn("AI Fluency", html)
        # the archive-only prompts were actually analyzed (live had none)
        self.assertIn("sessions in your archive", html)

    def test_smaller_live_never_truncates_larger_archive(self):
        # If the live file is SMALLER than the archive (corruption / truncation), the archive
        # must NOT be overwritten — the bigger copy is the more complete history.
        insight.archive_transcripts([self.f], self.arch)
        dest = os.path.join(self.arch, "proj", "sess.jsonl")
        with open(dest, "a", encoding="utf-8") as fh:        # grow the ARCHIVE past live
            fh.write(user_text("extra archived turn that live no longer has") + "\n")
        big = os.path.getsize(dest)
        new, updated = insight.archive_transcripts([self.f], self.arch)
        self.assertEqual((new, updated), (0, 0))             # skipped — archive already bigger
        self.assertEqual(os.path.getsize(dest), big)         # archive untouched

    def test_dedupe_survives_project_folder_rename(self):
        # Same session (same UUID filename) under two DIFFERENT project folders must dedupe to one.
        a = write_session(os.path.join(self.live, "proj"), "uuid-1.jsonl", [user_text("one"), user_text("two")])
        d2 = os.path.join(self.arch, "renamed-proj")
        os.makedirs(d2, exist_ok=True)
        b = write_session(d2, "uuid-1.jsonl", [user_text("one")])   # smaller copy, different folder
        merged = insight._dedupe_sessions([a, b])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0], a)                       # the larger one wins

    def test_no_archive_flag_does_not_write(self):
        live_dir = tempfile.mkdtemp()
        os.makedirs(os.path.join(live_dir, "proj"), exist_ok=True)
        write_session(os.path.join(live_dir, "proj"), "s.jsonl",
                      [user_text("add a /health route to server.py and run the tests")])
        out = os.path.join(live_dir, "r.html")
        os.environ["CLAUDE_PROJECTS_DIR"] = live_dir
        try:
            rc = insight.main(["--no-archive", "--archive", self.arch, "-o", out, "--no-open"])
        finally:
            del os.environ["CLAUDE_PROJECTS_DIR"]
        self.assertEqual(rc, 0)
        self.assertEqual(glob.glob(os.path.join(self.arch, "**", "*.jsonl"), recursive=True), [])

    def test_explicit_path_does_not_touch_archive(self):
        # Seed an archive, then analyze an explicit dir: the archive must be neither written nor merged.
        os.makedirs(os.path.join(self.arch, "old"), exist_ok=True)
        write_session(os.path.join(self.arch, "old"), "old.jsonl", [user_text("archived only")])
        before = sorted(glob.glob(os.path.join(self.arch, "**", "*.jsonl"), recursive=True))
        explicit = tempfile.mkdtemp()
        write_session(explicit, "live.jsonl",
                      [user_text("add a /health route to server.py and run the tests")])
        out = os.path.join(explicit, "r.html")
        rc = insight.main([explicit, "--archive", self.arch, "-o", out, "--no-open"])
        self.assertEqual(rc, 0)
        after = sorted(glob.glob(os.path.join(self.arch, "**", "*.jsonl"), recursive=True))
        self.assertEqual(before, after)                      # archive untouched
        with open(out, encoding="utf-8") as fh:
            html = fh.read()
        self.assertNotIn("sessions in your archive", html)   # archive not merged into analysis


class TestDiscovery(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_subagent_transcripts_are_excluded(self):
        # Agent-to-agent transcripts under .../subagents/... are NOT the user's prompts and
        # must not be discovered — otherwise running workflows would inflate the analysis.
        proj = os.path.join(self.tmp, "proj")
        sub = os.path.join(proj, "uuid", "subagents")
        os.makedirs(sub, exist_ok=True)
        main_f = write_session(proj, "main.jsonl", [user_text("a real user prompt about server.py")])
        sub_f = write_session(sub, "agent-x.jsonl", [user_text("do the assigned subtask")])
        found = insight.discover_files(self.tmp)
        self.assertIn(main_f, found)
        self.assertNotIn(sub_f, found)

    def test_explicit_single_subagent_file_is_still_honored(self):
        sub = os.path.join(self.tmp, "uuid", "subagents")
        os.makedirs(sub, exist_ok=True)
        sub_f = write_session(sub, "agent-x.jsonl", [user_text("explicitly requested file")])
        self.assertEqual(insight.discover_files(sub_f), [sub_f])


class TestPipelineModes(unittest.TestCase):
    """The --evidence (pipeline input) and --analysis (Opus output → report) hooks."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        write_session(self.tmp, "s.jsonl", [
            user_text("add a /health endpoint to server.py, only that file, so the LB can probe it"),
            assistant_tool("Read", file_path="/x/server.py"),
            assistant_tool("Edit", file_path="/x/server.py"),
            assistant_tool("Bash", command="python -m pytest -q"),
            user_text("run it and tell me if it passes"),
        ])

    def test_evidence_bundle_is_valid_and_self_contained(self):
        ev = os.path.join(self.tmp, "ev.json")
        rc = insight.main([self.tmp, "--evidence", ev, "--no-open", "-o", os.path.join(self.tmp, "r.html")])
        self.assertEqual(rc, 0)
        with open(ev, encoding="utf-8") as fh:
            d = json.load(fh)
        self.assertEqual(d["schema"], "claude-insight-evidence/1")
        for k in ("meta", "scores", "dimension_detail", "behavior", "archetype"):
            self.assertIn(k, d)
        self.assertGreaterEqual(len(d["behavior"]["sample_prompts"]), 1)
        self.assertIn("Direction", d["behavior"]["weak_examples"])
        # evidence must carry file basenames, never absolute paths
        for items in d["behavior"]["weak_examples"].values():
            for e in items:
                self.assertNotIn("/", e.get("file", ""))

    def test_analysis_json_merges_into_report(self):
        analysis = {
            "overall_read": "You hand off whole jobs well; sharpen your briefs next.",
            "skill_map": [
                {"competency": "Delegation", "level": 4, "level_label": "Advanced",
                 "summary": "Hands off end to end.", "evidence": ["one scoped hand-off"],
                 "next_move": "add one sentence of intent per hand-off"},
                {"competency": "Description", "level": 2, "level_label": "Developing",
                 "summary": "Often terse.", "evidence": ["'run it'"],
                 "next_move": "name a file + a constraint"},
            ],
            "top_growth": [{"title": "Brief better", "why": "fewer rounds", "how": "front-load intent",
                            "example_before": "run it", "example_after": "run the server.py tests; report failures"}],
            "strengths": ["clear delegation"],
        }
        ap = os.path.join(self.tmp, "an.json")
        with open(ap, "w", encoding="utf-8") as fh:
            json.dump(analysis, fh)
        out = os.path.join(self.tmp, "r.html")
        rc = insight.main([self.tmp, "--analysis", ap, "--no-open", "-o", out])
        self.assertEqual(rc, 0)
        with open(out, encoding="utf-8") as fh:
            html = fh.read()
        self.assertIn("analyzed against the AI Fluency framework", html)
        self.assertIn("Delegation", html)
        self.assertIn("Advanced", html)
        self.assertIn("name a file + a constraint", html)

    def test_report_without_analysis_has_no_ai_section(self):
        out = os.path.join(self.tmp, "r.html")
        rc = insight.main([self.tmp, "--no-open", "-o", out])
        self.assertEqual(rc, 0)
        with open(out, encoding="utf-8") as fh:
            html = fh.read()
        self.assertNotIn("analyzed against the AI Fluency framework", html)
        # A plain deterministic run must NOT show the "AI stage didn't run" banner — that
        # banner is only for a run where an analysis was supplied but couldn't be used.
        self.assertNotIn("Deterministic report only", html)


class TestAnalysisProvenance(unittest.TestCase):
    """Regression guard for the leakage bug: a stale/foreign/empty analysis must never
    render in this run's report. An analysis is bound to a run by a fingerprint."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        write_session(self.tmp, "s.jsonl", [
            user_text("add a /health endpoint to server.py, only that file, so the LB can probe it"),
            assistant_tool("Read", file_path="/x/server.py"),
            assistant_tool("Edit", file_path="/x/server.py"),
            assistant_tool("Bash", command="python -m pytest -q"),
            user_text("run it and tell me if it passes"),
        ])
        self.fp = insight.analyze(insight.parse(insight.discover_files(self.tmp)))["fingerprint"]

    def _analysis(self, **over):
        a = {
            "overall_read": "UNIQUE-VERDICT-TOKEN: hands off whole jobs well.",
            "skill_map": [{"competency": "Delegation", "level": 4, "level_label": "Advanced",
                           "summary": "Hands off end to end.", "evidence": ["one scoped hand-off"],
                           "next_move": "add one sentence of intent per hand-off"}],
            "top_growth": [], "strengths": ["clear delegation"],
        }
        a.update(over)
        return a

    def _write(self, obj):
        p = os.path.join(self.tmp, "an.json")
        with open(p, "w", encoding="utf-8") as fh:
            json.dump(obj, fh)
        return p

    def _run(self, ap, evidence=None):
        out = os.path.join(self.tmp, "r.html")
        argv = [self.tmp, "--analysis", ap, "--no-open", "-o", out]
        if evidence:
            argv += ["--analysis-evidence", evidence]
        rc = insight.main(argv)
        self.assertEqual(rc, 0)
        with open(out, encoding="utf-8") as fh:
            return fh.read()

    def _evidence_for(self, src_dir):
        """Write a real evidence bundle (with its run_fingerprint) for a transcript dir."""
        evp = os.path.join(tempfile.mkdtemp(), "ev.json")
        rc = insight.main([src_dir, "--evidence", evp, "--no-open", "--no-archive",
                           "-o", os.path.join(self.tmp, "det.html")])
        self.assertEqual(rc, 0)
        return evp

    def test_matching_fingerprint_merges(self):
        html = self._run(self._write(self._analysis(run_fingerprint=self.fp)))
        self.assertIn("analyzed against the AI Fluency framework", html)
        self.assertIn("UNIQUE-VERDICT-TOKEN", html)

    def test_mismatched_fingerprint_is_rejected_and_does_not_leak(self):
        # The exact reported bug: an analysis from a DIFFERENT run/person must not render.
        html = self._run(self._write(self._analysis(run_fingerprint="deadbeefdeadbeef")))
        self.assertNotIn("UNIQUE-VERDICT-TOKEN", html)              # no foreign verdict leaked
        self.assertNotIn("analyzed against the AI Fluency framework", html)
        self.assertIn("Deterministic report only", html)           # and we say so honestly

    def test_empty_analysis_is_dropped_with_notice(self):
        html = self._run(self._write({}))
        self.assertNotIn("analyzed against the AI Fluency framework", html)
        self.assertIn("Deterministic report only", html)

    def test_fingerprintless_analysis_still_merges_for_backcompat(self):
        # No run_fingerprint (older analyses / manual use) is allowed through unchanged.
        html = self._run(self._write(self._analysis()))
        self.assertIn("analyzed against the AI Fluency framework", html)

    def test_fingerprint_changes_with_the_data(self):
        other = tempfile.mkdtemp()
        write_session(other, "s.jsonl", [user_text("totally different prompt here")])
        fp2 = insight.analyze(insight.parse(insight.discover_files(other)))["fingerprint"]
        self.assertNotEqual(self.fp, fp2)

    def test_evidence_binding_matching_merges(self):
        # The real-pipeline path: --analysis-evidence is the bundle this run produced, so its
        # fingerprint matches and the (fingerprint-less) analysis merges — no LLM copy needed.
        ev = self._evidence_for(self.tmp)
        html = self._run(self._write(self._analysis()), evidence=ev)
        self.assertIn("analyzed against the AI Fluency framework", html)
        self.assertIn("UNIQUE-VERDICT-TOKEN", html)

    def test_evidence_binding_mismatch_rejects_and_does_not_leak(self):
        # Evidence built from DIFFERENT data: the fingerprint won't match this run, so even a
        # well-formed analysis is refused — this is what stops one person's verdict leaking.
        other = tempfile.mkdtemp()
        write_session(other, "s.jsonl", [user_text("an entirely different person's session"),
                                         user_text("with different prompts entirely")])
        foreign_ev = self._evidence_for(other)
        html = self._run(self._write(self._analysis()), evidence=foreign_ev)
        self.assertNotIn("UNIQUE-VERDICT-TOKEN", html)
        self.assertNotIn("analyzed against the AI Fluency framework", html)
        self.assertIn("Deterministic report only", html)


class TestNoTemplateMisframing(unittest.TestCase):
    """The deterministic report's generic teaching examples must be labeled as generic,
    and each user's report must carry that user's own evidence, not a shared template."""

    def _report_for(self, prompts):
        d = tempfile.mkdtemp()
        write_session(d, "s.jsonl", [user_text(p) for p in prompts])
        out = os.path.join(d, "r.html")
        rc = insight.main([d, "--no-open", "-o", out])
        self.assertEqual(rc, 0)
        with open(out, encoding="utf-8") as fh:
            return fh.read()

    def test_generic_examples_are_labeled_not_personalized(self):
        html = self._report_for(["fix it", "do the thing", "make it work", "change that"])
        # the canned before/after pairs must be explicitly flagged as not-from-your-sessions
        self.assertIn("not</b> from your sessions", html)

    def test_two_different_users_get_their_own_evidence(self):
        a = self._report_for(["fix the frobnicator", "fix the frobnicator now",
                              "update the frobnicator", "redo the frobnicator"])
        b = self._report_for(["build the gizmotron", "build the gizmotron now",
                              "update the gizmotron", "redo the gizmotron"])
        # each report surfaces its OWN distinctive prompts and not the other user's
        self.assertIn("frobnicator", a)
        self.assertNotIn("gizmotron", a)
        self.assertIn("gizmotron", b)
        self.assertNotIn("frobnicator", b)


class TestPersonalizedGrowthAndQuiet(unittest.TestCase):
    """The finished report must show Opus's TAILORED growth moves (not the generic teaching
    examples), and the measure pass must be silent so the score isn't surfaced early."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        write_session(self.tmp, "s.jsonl", [
            user_text("add a /health endpoint to server.py, only that file, so the LB can probe it"),
            assistant_tool("Read", file_path="/x/server.py"),
            assistant_tool("Edit", file_path="/x/server.py"),
            user_text("run it"),
        ])

    def test_quiet_suppresses_the_score_summary(self):
        out = os.path.join(self.tmp, "r.html")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = insight.main([self.tmp, "--no-open", "--quiet", "-o", out])
        self.assertEqual(rc, 0)
        self.assertNotIn("AI Fluency Score", buf.getvalue())   # nothing surfaced to the user
        self.assertTrue(os.path.exists(out))                   # but the report was still written

    def test_opus_top_growth_replaces_generic_examples(self):
        analysis = {
            "overall_read": "Strong delegator; sharpen your briefs.",
            "skill_map": [
                {"competency": "Delegation", "level": 4, "level_label": "Advanced",
                 "summary": "Hands off whole jobs.", "evidence": ["scoped hand-off"], "next_move": "name intent"},
                {"competency": "Description", "level": 2, "level_label": "Developing",
                 "summary": "Terse.", "evidence": ["'run it'"], "next_move": "name a file + a constraint"},
                {"competency": "Discernment", "level": 3, "level_label": "Proficient",
                 "summary": "Reads first.", "evidence": ["read before edit"], "next_move": "verify after edits"},
                {"competency": "Diligence", "level": 3, "level_label": "Proficient",
                 "summary": "Owns sequencing.", "evidence": ["phase gate"], "next_move": "tear down"},
            ],
            "top_growth": [
                {"title": "Put a finish line on every hand-off", "why": "Your intent rate is low",
                 "how": "name what 'done' looks like",
                 "example_before": "run it",
                 "example_after": "TAILORED-REWRITE-TOKEN: run the server.py tests and paste the output"},
            ],
            "strengths": ["clear delegation"],
        }
        ap = os.path.join(self.tmp, "an.json")
        with open(ap, "w", encoding="utf-8") as fh:
            json.dump(analysis, fh)
        out = os.path.join(self.tmp, "r.html")
        rc = insight.main([self.tmp, "--analysis", ap, "--no-open", "-o", out])
        self.assertEqual(rc, 0)
        with open(out, encoding="utf-8") as fh:
            html = fh.read()
        # the personalized growth card is rendered, with Opus's tailored rewrite of a real prompt
        self.assertIn("TAILORED-REWRITE-TOKEN", html)
        self.assertIn("written for you", html)
        self.assertIn("Tailored rewrite for you", html)
        # and the generic stock example is NOT in the improve section anymore
        self.assertNotIn("session cookie", html)

    def test_without_analysis_generic_examples_are_present_but_labeled(self):
        out = os.path.join(self.tmp, "r.html")
        rc = insight.main([self.tmp, "--no-open", "-o", out])
        self.assertEqual(rc, 0)
        with open(out, encoding="utf-8") as fh:
            html = fh.read()
        # no AI ran -> the generic teaching examples appear, explicitly flagged as generic
        self.assertIn("not</b> from your sessions", html)


if __name__ == "__main__":
    unittest.main()
