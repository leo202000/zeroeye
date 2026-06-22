"""
Tests for log aggregator JSONL output (bounty #3).

Covers JSONL export correctness: one JSON object per line, valid JSON
on each line, field round-tripping, max_entries cap, empty export, and
CLI --format jsonl integration.
"""

import sys
import json
import os
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))

from log_aggregator import LogAggregator, parse_args


def _populate(agg, n=5):
    for i in range(n):
        agg._parse_line(f"2026-06-22 10:0{i}:00 ERROR [api] error number {i}")
    return agg


class TestExportJsonl(unittest.TestCase):
    def test_creates_valid_jsonl(self):
        agg = _populate(LogAggregator(), 3)
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            path = f.name
        try:
            agg.export_jsonl(path)
            with open(path, "r", encoding="utf-8") as fh:
                lines = fh.readlines()
            self.assertEqual(len(lines), 3)
            for line in lines:
                obj = json.loads(line)
                self.assertIsInstance(obj, dict)
                self.assertIn("level", obj)
                self.assertIn("message", obj)
                self.assertEqual(obj["level"], "error")
        finally:
            os.unlink(path)

    def test_each_line_is_self_contained(self):
        agg = _populate(LogAggregator(), 4)
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            path = f.name
        try:
            agg.export_jsonl(path)
            with open(path, "r", encoding="utf-8") as fh:
                content = fh.read()
            # every line must be valid JSON independently
            for line in content.strip().split("\n"):
                obj = json.loads(line)
                self.assertIn("format", obj)
                self.assertIn("timestamp", obj)
        finally:
            os.unlink(path)

    def test_max_entries_cap(self):
        agg = _populate(LogAggregator(), 10)
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            path = f.name
        try:
            agg.export_jsonl(path, max_entries=3)
            with open(path, "r", encoding="utf-8") as fh:
                lines = fh.readlines()
            self.assertEqual(len(lines), 3)
        finally:
            os.unlink(path)

    def test_empty_aggregator(self):
        agg = LogAggregator()
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            path = f.name
        try:
            agg.export_jsonl(path)
            with open(path, "r", encoding="utf-8") as fh:
                content = fh.read()
            self.assertEqual(content, "")
        finally:
            os.unlink(path)

    def test_field_roundtrip(self):
        agg = LogAggregator()
        agg._parse_line('{"timestamp":1700000000,"level":"warn","service":"auth","message":"rate limited"}')
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            path = f.name
        try:
            agg.export_jsonl(path)
            with open(path, "r", encoding="utf-8") as fh:
                obj = json.loads(fh.readline())
            self.assertEqual(obj["level"], "warn")
            self.assertEqual(obj["service"], "auth")
            self.assertEqual(obj["message"], "rate limited")
            self.assertEqual(obj["format"], "json")
        finally:
            os.unlink(path)


class TestCliFormatJsonl(unittest.TestCase):
    def test_format_accepts_jsonl(self):
        old_argv = sys.argv
        sys.argv = ["log_aggregator.py", "--format", "jsonl", "-o", os.devnull]
        try:
            args = parse_args()
            self.assertEqual(args.format, "jsonl")
        finally:
            sys.argv = old_argv


if __name__ == "__main__":
    unittest.main()
