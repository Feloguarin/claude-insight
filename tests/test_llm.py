"""Tests for the local LLM (Ollama) analyzer, using a stub HTTP server."""

import json
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from claude_insight.analyzer.llm import LocalLLMAnalyzer
from claude_insight.analyzer.metrics import AggregateMetrics


CANNED = {
    "archetype": "Architect",
    "archetype_reason": "Plans before building.",
    "summary": "You design first.",
    "recommendations": ["one", "two", "three", "four"],
}


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send(self, obj):
        body = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self._send({"models": [{"name": "gemma3:4b"}]})

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length)
        self._send({"message": {"content": json.dumps(CANNED)}})


class StubServer:
    def __enter__(self):
        self.srv = HTTPServer(("127.0.0.1", 0), _Handler)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        return f"http://127.0.0.1:{self.srv.server_address[1]}"

    def __exit__(self, *args):
        self.srv.shutdown()
        self.srv.server_close()


class LLMTests(unittest.TestCase):
    def _metrics(self):
        m = AggregateMetrics(total_sessions=1, total_prompts=2)
        m.tool_usage = {"Read": 2}
        return m

    def test_available_and_analyze(self):
        with StubServer() as host:
            llm = LocalLLMAnalyzer(model="gemma3:4b", host=host)
            self.assertTrue(llm.is_available())
            insights = llm.analyze(self._metrics(), ["design the system", "plan it"])
            self.assertIsNotNone(insights)
            self.assertEqual(insights.archetype, "🏗️ Architect")
            self.assertEqual(insights.summary, "You design first.")
            # Recommendations are capped at 3.
            self.assertEqual(len(insights.recommendations), 3)
            self.assertEqual(insights.model, "gemma3:4b")

    def test_unavailable_when_server_down(self):
        # Port 1 is not listening; is_available must be False, not raise.
        llm = LocalLLMAnalyzer(model="gemma3:4b", host="http://127.0.0.1:1")
        self.assertFalse(llm.is_available())
        self.assertIsNone(llm.analyze(self._metrics(), ["x"]))

    def test_host_without_scheme_is_normalized(self):
        llm = LocalLLMAnalyzer(host="localhost:11434")
        self.assertTrue(llm.host.startswith("http://"))


if __name__ == "__main__":
    unittest.main()
