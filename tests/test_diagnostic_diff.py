"""
Tests for the diagnostic metadata diff tool (bounty #5).

Covers metadata field changes, module status transitions, added/removed
modules, identical reports, JSON output mode, and CLI argument parsing.
"""

import sys
import json
import os
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))

from diagnostic_diff import diff_reports, format_diff, load_report, parse_args


def _report(modules=None, **overrides):
    base = {
        "generated_at": "2026-06-22T10:00:00+00:00",
        "commit": "aaa11111",
        "diagnostic_logd": "diagnostic/build-aaa11111.logd",
        "total_modules": 3,
        "passed": 2,
        "failed": 1,
        "chunked": False,
        "modules": modules or [],
    }
    base.update(overrides)
    return base


def _module(name, status="PASS", elapsed=1.0):
    return {"name": name, "status": status, "elapsed_seconds": elapsed, "artifact": None, "output": ""}


class TestDiffReports(unittest.TestCase):
    def test_identical_reports_no_changes(self):
        old = _report([_module("backend"), _module("frontend")])
        new = _report([_module("backend"), _module("frontend")])
        diff = diff_reports(old, new)
        self.assertEqual(diff["metadata_changes"], [])
        self.assertEqual(diff["modules_added"], [])
        self.assertEqual(diff["modules_removed"], [])
        self.assertEqual(diff["module_changes"], [])

    def test_metadata_change_detected(self):
        old = _report([_module("backend")], commit="aaa11111", passed=2, failed=1)
        new = _report([_module("backend")], commit="bbb22222", passed=1, failed=2)
        diff = diff_reports(old, new)
        fields = {c["field"] for c in diff["metadata_changes"]}
        self.assertIn("commit", fields)
        self.assertIn("passed", fields)
        self.assertIn("failed", fields)

    def test_module_status_transition(self):
        old = _report([_module("backend", "FAIL"), _module("frontend", "PASS")])
        new = _report([_module("backend", "PASS"), _module("frontend", "PASS")])
        diff = diff_reports(old, new)
        changed = {e["module"]: e["changes"] for e in diff["module_changes"]}
        self.assertIn("backend", changed)
        status_change = [c for c in changed["backend"] if c["field"] == "status"]
        self.assertEqual(len(status_change), 1)
        self.assertEqual(status_change[0]["old"], "FAIL")
        self.assertEqual(status_change[0]["new"], "PASS")
        self.assertNotIn("frontend", changed)

    def test_module_added(self):
        old = _report([_module("backend")])
        new = _report([_module("backend"), _module("market")])
        diff = diff_reports(old, new)
        self.assertEqual(diff["modules_added"], ["market"])

    def test_module_removed(self):
        old = _report([_module("backend"), _module("market")])
        new = _report([_module("backend")])
        diff = diff_reports(old, new)
        self.assertEqual(diff["modules_removed"], ["market"])

    def test_elapsed_seconds_change(self):
        old = _report([_module("backend", elapsed=1.0)])
        new = _report([_module("backend", elapsed=2.5)])
        diff = diff_reports(old, new)
        changed = {e["module"]: e["changes"] for e in diff["module_changes"]}
        elapsed_change = [c for c in changed["backend"] if c["field"] == "elapsed_seconds"]
        self.assertEqual(len(elapsed_change), 1)
        self.assertEqual(elapsed_change[0]["old"], 1.0)
        self.assertEqual(elapsed_change[0]["new"], 2.5)

    def test_multiple_changes(self):
        old = _report([_module("backend", "FAIL"), _module("frontend", "PASS")], commit="aaa")
        new = _report([_module("backend", "PASS"), _module("frontend", "FAIL"), _module("market")], commit="bbb")
        diff = diff_reports(old, new)
        self.assertEqual(diff["modules_added"], ["market"])
        changed_names = {e["module"] for e in diff["module_changes"]}
        self.assertEqual(changed_names, {"backend", "frontend"})
        meta_fields = {c["field"] for c in diff["metadata_changes"]}
        self.assertIn("commit", meta_fields)


class TestFormatDiff(unittest.TestCase):
    def test_format_contains_sections(self):
        old = _report([_module("backend", "FAIL")], commit="aaa")
        new = _report([_module("backend", "PASS"), _module("market")], commit="bbb")
        text = format_diff(diff_reports(old, new))
        self.assertIn("Diagnostic Metadata Diff", text)
        self.assertIn("Metadata changes:", text)
        self.assertIn("commit:", text)
        self.assertIn("Modules added:", text)
        self.assertIn("Module status changes:", text)
        self.assertIn("backend:", text)


class TestLoadAndCli(unittest.TestCase):
    def test_load_report(self):
        report = _report([_module("backend")])
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump(report, f)
            path = f.name
        try:
            loaded = load_report(path)
            self.assertEqual(loaded["commit"], report["commit"])
        finally:
            os.unlink(path)

    def test_cli_accepts_json_flag(self):
        old_argv = sys.argv
        sys.argv = ["diagnostic_diff.py", "old.json", "new.json", "--json"]
        try:
            args = parse_args()
            self.assertTrue(args.json)
            self.assertEqual(args.old, "old.json")
            self.assertEqual(args.new, "new.json")
        finally:
            sys.argv = old_argv


if __name__ == "__main__":
    unittest.main()
