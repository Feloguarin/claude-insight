"""
Claude Code JSONL Transcript Parser

Parses Claude Code session files (stored as JSONL in ~/.claude/projects/)
into structured data for analysis.

No network calls. No external APIs. Pure local parsing.
"""

import json
import os
from pathlib import Path
from typing import Iterator, Optional
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Message:
    """A single message in a Claude Code session."""
    role: str
    content: str = ""
    tool_calls: list = field(default_factory=list)
    tool_results: list = field(default_factory=list)
    timestamp: Optional[str] = None
    thinking: str = ""
    
    @property
    def is_user(self) -> bool:
        return self.role == "user"
    
    @property
    def is_assistant(self) -> bool:
        return self.role == "assistant"
    
    @property
    def has_tools(self) -> bool:
        return len(self.tool_calls) > 0

    @property
    def parsed_time(self) -> Optional[datetime]:
        """Parse the ISO 8601 timestamp into a datetime, if present and valid."""
        if not self.timestamp:
            return None
        try:
            # Claude Code writes UTC timestamps like "2024-01-01T12:00:00.000Z"
            return datetime.fromisoformat(self.timestamp.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            return None


@dataclass
class ToolCall:
    """A tool invocation from the AI."""
    tool_type: str
    arguments: dict = field(default_factory=dict)
    result: Optional[str] = None


@dataclass
class Session:
    """A complete Claude Code session."""
    session_id: str
    messages: list = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    
    # Computed properties
    @property
    def total_messages(self) -> int:
        return len(self.messages)
    
    @property
    def user_messages(self) -> list:
        return [m for m in self.messages if m.is_user]
    
    @property
    def assistant_messages(self) -> list:
        return [m for m in self.messages if m.is_assistant]
    
    @property
    def total_prompts(self) -> int:
        return len(self.user_messages)
    
    @property
    def total_tool_calls(self) -> int:
        return sum(len(m.tool_calls) for m in self.messages)

    @property
    def duration_minutes(self) -> float:
        """Real wall-clock duration from message timestamps.

        Falls back to a rough estimate (2.5 min/message) when the transcript
        has no usable timestamps.
        """
        times = [m.parsed_time for m in self.messages if m.parsed_time is not None]
        if len(times) >= 2:
            span = (max(times) - min(times)).total_seconds() / 60
            if span > 0:
                return span
        return self.total_messages * 2.5
    
    @property
    def avg_prompt_length(self) -> float:
        prompts = [m.content for m in self.user_messages if m.content]
        if not prompts:
            return 0.0
        return sum(len(p) for p in prompts) / len(prompts)
    
    @property
    def tool_usage(self) -> dict:
        """Count of each tool type used."""
        usage = {}
        for m in self.messages:
            for tc in m.tool_calls:
                tool_type = tc.get("type", "unknown")
                usage[tool_type] = usage.get(tool_type, 0) + 1
        return usage


class TranscriptParser:
    """Parser for Claude Code JSONL transcript files."""
    
    DEFAULT_PATHS = [
        "~/.claude/projects",
        "~/.claude/sessions",
    ]
    
    def __init__(self, transcript_dir: Optional[str] = None):
        self.transcript_dir = Path(transcript_dir).expanduser() if transcript_dir else None
    
    def find_transcript_files(self) -> list[Path]:
        """Find all JSONL transcript files in the transcript directory."""
        files = []
        
        if self.transcript_dir:
            # Expand and resolve the paths to be absolute
            search_path = self.transcript_dir.expanduser().resolve()
            if search_path.exists():
                if search_path.is_file() and search_path.suffix == ".jsonl":
                    files.append(search_path)
                elif search_path.is_dir():
                    files.extend(search_path.glob("*.jsonl"))
                    files.extend(search_path.rglob("*.jsonl"))
        
        # Only search default paths when no directory was explicitly given.
        # An explicit (but empty) --dir should find nothing, not silently fall
        # back to ~/.claude.
        if self.transcript_dir is None:
            for default_path in self.DEFAULT_PATHS:
                path = Path(default_path).expanduser()
                if path.exists():
                    files.extend(path.rglob("*.jsonl"))
        
        # Filter out duplicates and return
        return sorted(list(set(files)))
    
    def parse_file(self, filepath: Path) -> Session:
        """Parse a single JSONL transcript file."""
        messages = []
        session_id = filepath.stem
        metadata = {}
        
        with open(filepath, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                
                # Handle different Claude Code JSONL formats
                msg = self._parse_entry(entry)
                if msg:
                    messages.append(msg)
        
        return Session(
            session_id=session_id,
            messages=messages,
            metadata=metadata
        )
    
    def _extract_content(self, content_val) -> tuple:
        """Normalize a content value into (text, tool_calls, thinking).

        Handles a plain string, or a list of content blocks (text / thinking /
        tool_use) as used by real Claude Code transcripts. Always returns a
        string for text content so downstream code never sees a raw list.
        """
        content = ""
        tool_calls = []
        thinking = ""

        if isinstance(content_val, str):
            content = content_val
        elif isinstance(content_val, list):
            for block in content_val:
                if not isinstance(block, dict):
                    continue
                block_type = block.get("type")
                if block_type == "text":
                    content += block.get("text", "")
                elif block_type == "thinking":
                    thinking += block.get("thinking", "")
                elif block_type == "tool_use":
                    tool_calls.append({
                        "type": block.get("name", "unknown"),
                        "arguments": block.get("input", {}),
                    })

        return content, tool_calls, thinking

    def _parse_entry(self, entry: dict) -> Optional[Message]:
        """Parse a single JSONL entry into a Message."""
        if not isinstance(entry, dict):
            return None
        
        # Claude Code format: each line is a conversation turn
        role = entry.get("role", "")
        thinking = ""
        timestamp = entry.get("timestamp")

        # Pick the content source. Real Claude Code transcripts nest it under
        # "message" (with content often a list of blocks); other formats put it
        # at the top level. Either way, run it through the same extractor.
        content_val = None
        if isinstance(entry.get("message"), dict):
            msg_data = entry["message"]
            role = msg_data.get("role", role)
            content_val = msg_data.get("content")
        elif "content" in entry:
            content_val = entry["content"]

        content, tool_calls, block_thinking = self._extract_content(content_val)
        if block_thinking:
            thinking = block_thinking

        # Extract tool calls if present
        if "tool_calls" in entry:
            for tc in entry["tool_calls"]:
                if isinstance(tc, dict):
                    tool_calls.append({
                        "type": tc.get("function", {}).get("name", tc.get("type", "unknown")),
                        "arguments": tc.get("function", {}).get("arguments", tc.get("input", {}))
                    })
        
        # Extract thinking/reasoning
        if "thinking" in entry:
            thinking = entry["thinking"]
        if "reasoning" in entry:
            thinking = entry["reasoning"]
        
        # Determine role if not explicitly set
        if not role:
            entry_type = entry.get("type", "")
            if entry_type in ("user", "assistant"):
                role = entry_type
            elif "prompt" in entry:
                role = "user"
            elif "response" in entry:
                role = "assistant"
            elif tool_calls:
                role = "assistant"
        
        return Message(
            role=role,
            content=content,
            tool_calls=tool_calls,
            thinking=thinking,
            timestamp=timestamp
        )
    
    def parse_all(self) -> list[Session]:
        """Parse all found transcript files."""
        files = self.find_transcript_files()
        sessions = []
        
        for filepath in files:
            try:
                session = self.parse_file(filepath)
                if session.total_messages > 0:
                    sessions.append(session)
            except Exception as e:
                # Skip corrupted files
                print(f"Warning: Could not parse {filepath}: {e}", file=__import__('sys').stderr)
        
        return sessions
    
    def get_session_stats(self) -> dict:
        """Get basic stats about available sessions."""
        files = self.find_transcript_files()
        
        return {
            "total_files": len(files),
            "total_size_mb": sum(f.stat().st_size for f in files if f.exists()) / (1024 * 1024),
            "directories": list(set(str(f.parent) for f in files)),
        }
