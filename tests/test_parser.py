"""Tests for the JSONL transcript parser."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from claude_insight.parser.transcript import TranscriptParser, Session, Message


class MessageTests(unittest.TestCase):
    def test_role_helpers(self):
        self.assertTrue(Message(role="user").is_user)
        self.assertTrue(Message(role="assistant").is_assistant)
        self.assertFalse(Message(role="user").has_tools)
        self.assertTrue(Message(role="assistant", tool_calls=[{"type": "Read"}]).has_tools)

    def test_parsed_time_valid_and_invalid(self):
        self.assertIsNotNone(Message(role="user", timestamp="2024-01-01T12:00:00Z").parsed_time)
        self.assertIsNone(Message(role="user", timestamp="not-a-date").parsed_time)
        self.assertIsNone(Message(role="user").parsed_time)


class SessionTests(unittest.TestCase):
    def _session(self):
        return Session(session_id="s1", messages=[
            Message(role="user", content="hello world"),
            Message(role="assistant", content="hi", tool_calls=[{"type": "Read"}, {"type": "Edit"}]),
            Message(role="user", content="do a thing please"),
        ])

    def test_counts(self):
        s = self._session()
        self.assertEqual(s.total_messages, 3)
        self.assertEqual(s.total_prompts, 2)
        self.assertEqual(len(s.user_messages), 2)
        self.assertEqual(len(s.assistant_messages), 1)
        self.assertEqual(s.total_tool_calls, 2)

    def test_tool_usage(self):
        self.assertEqual(self._session().tool_usage, {"Read": 1, "Edit": 1})

    def test_avg_prompt_length(self):
        s = self._session()
        expected = (len("hello world") + len("do a thing please")) / 2
        self.assertAlmostEqual(s.avg_prompt_length, expected)

    def test_duration_real_from_timestamps(self):
        s = Session(session_id="t", messages=[
            Message(role="user", content="a", timestamp="2024-01-01T12:00:00Z"),
            Message(role="assistant", content="b", timestamp="2024-01-01T12:30:00Z"),
        ])
        self.assertAlmostEqual(s.duration_minutes, 30.0)

    def test_duration_fallback_without_timestamps(self):
        s = Session(session_id="t", messages=[
            Message(role="user", content="a"),
            Message(role="assistant", content="b"),
        ])
        self.assertAlmostEqual(s.duration_minutes, 2 * 2.5)


class TranscriptParserTests(unittest.TestCase):
    def _write(self, lines):
        tmp = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False)
        for line in lines:
            tmp.write(json.dumps(line) + "\n")
        tmp.flush()
        tmp.close()
        return Path(tmp.name)

    def test_parse_string_content_and_roles(self):
        path = self._write([
            {"role": "user", "content": "plan the module", "timestamp": "2024-01-01T10:00:00Z"},
            {"role": "assistant", "content": "ok"},
        ])
        session = TranscriptParser().parse_file(path)
        self.assertEqual(session.total_messages, 2)
        self.assertEqual(session.user_messages[0].content, "plan the module")
        self.assertEqual(session.user_messages[0].timestamp, "2024-01-01T10:00:00Z")

    def test_parse_block_list_content_and_tool_use(self):
        path = self._write([
            {"role": "assistant", "content": [
                {"type": "text", "text": "let me look"},
                {"type": "thinking", "thinking": "internal"},
                {"type": "tool_use", "name": "Grep", "input": {"q": "x"}},
            ]},
        ])
        session = TranscriptParser().parse_file(path)
        msg = session.messages[0]
        self.assertEqual(msg.content, "let me look")
        self.assertEqual(msg.thinking, "internal")
        self.assertEqual(msg.tool_calls[0]["type"], "Grep")

    def test_parse_alt_tool_calls_format(self):
        path = self._write([
            {"role": "assistant", "content": "debug",
             "tool_calls": [{"function": {"name": "grep", "arguments": {"q": "y"}}}]},
        ])
        session = TranscriptParser().parse_file(path)
        self.assertEqual(session.messages[0].tool_calls[0]["type"], "grep")

    def test_real_claude_code_nested_message_format(self):
        # The real Claude Code transcript shape: role + content live under
        # "message", content is a list of blocks, role hinted by top-level type.
        path = self._write([
            {"type": "user", "message": {"role": "user", "content": "plan it"},
             "timestamp": "2024-01-01T10:00:00Z"},
            {"type": "assistant", "message": {"role": "assistant", "content": [
                {"type": "text", "text": "looking now"},
                {"type": "tool_use", "name": "Read", "input": {"path": "x.py"}},
            ]}},
        ])
        session = TranscriptParser().parse_file(path)
        self.assertEqual(session.total_messages, 2)
        self.assertEqual(session.total_prompts, 1)
        # Critically: text content is a STRING, not a raw list (the old bug),
        # so downstream .lower() etc. don't crash.
        for msg in session.messages:
            self.assertIsInstance(msg.content, str)
        self.assertEqual(session.assistant_messages[0].content, "looking now")
        self.assertEqual(session.tool_usage, {"Read": 1})

    def test_explicit_dir_does_not_fall_back_to_defaults(self):
        empty = Path(tempfile.mkdtemp())
        self.assertEqual(TranscriptParser(str(empty)).find_transcript_files(), [])

    def test_corrupted_lines_skipped(self):
        tmp = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False)
        tmp.write('{"role": "user", "content": "ok"}\n')
        tmp.write("this is not json\n")
        tmp.write("\n")
        tmp.flush()
        tmp.close()
        session = TranscriptParser().parse_file(Path(tmp.name))
        self.assertEqual(session.total_messages, 1)

    def test_find_files_in_directory(self):
        d = Path(tempfile.mkdtemp())
        (d / "a.jsonl").write_text('{"role":"user","content":"hi"}\n')
        (d / "b.jsonl").write_text('{"role":"user","content":"yo"}\n')
        (d / "ignore.txt").write_text("nope\n")
        parser = TranscriptParser(str(d))
        files = parser.find_transcript_files()
        self.assertEqual(len(files), 2)

    def test_parse_all_skips_empty_sessions(self):
        d = Path(tempfile.mkdtemp())
        (d / "good.jsonl").write_text('{"role":"user","content":"hi"}\n')
        (d / "empty.jsonl").write_text("\n\n")
        sessions = TranscriptParser(str(d)).parse_all()
        self.assertEqual(len(sessions), 1)


if __name__ == "__main__":
    unittest.main()
