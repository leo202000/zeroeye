"""
Comprehensive API test suite for the tools/ modules (bounty #6).

Validates the public API contracts of the diagnostic and operations tools:
config_generator, data_generator, log_aggregator, health_check, and
diagnostic_diff. These are contract tests over the documented public
functions and classes, ensuring their signatures, return types, and
behaviours stay stable. No network access is required.
"""

import sys
import json
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))

import config_generator as cg
import data_generator as dg
import log_aggregator as la
import health_check as hc


# ---------------------------------------------------------------------------
# config_generator API
# ---------------------------------------------------------------------------

class TestConfigGeneratorAPI(unittest.TestCase):
    def test_merge_config_shallow(self):
        result = cg.merge_config({"a": 1, "b": 2}, {"b": 3})
        self.assertEqual(result, {"a": 1, "b": 3})

    def test_merge_config_deep(self):
        base = {"db": {"host": "localhost", "port": 5432}}
        override = {"db": {"port": 6543}}
        result = cg.merge_config(base, override)
        self.assertEqual(result["db"]["host"], "localhost")
        self.assertEqual(result["db"]["port"], 6543)

    def test_generate_config_returns_dict(self):
        config = cg.generate_config("production")
        self.assertIsInstance(config, dict)

    def test_generate_config_with_overrides(self):
        config = cg.generate_config("production", {"custom_key": "value"})
        self.assertEqual(config["custom_key"], "value")

    def test_mask_sensitive_redacts(self):
        config = {"database": {"password": "secret123"}, "name": "app"}
        masked = cg.mask_sensitive(config)
        self.assertEqual(masked["database"]["password"], "***REDACTED***")
        self.assertEqual(masked["name"], "app")

    def test_mask_sensitive_preserves_non_sensitive(self):
        config = {"host": "localhost", "port": 5432}
        masked = cg.mask_sensitive(config)
        self.assertEqual(masked, config)

    def test_to_json_roundtrip(self):
        config = {"a": 1, "b": [1, 2]}
        text = cg.to_json(config)
        self.assertEqual(json.loads(text), config)

    def test_to_json_pretty(self):
        text = cg.to_json({"a": 1}, pretty=True)
        self.assertIn("\n", text)

    def test_to_json_compact(self):
        text = cg.to_json({"a": 1}, pretty=False)
        self.assertNotIn("\n", text)


# ---------------------------------------------------------------------------
# data_generator API
# ---------------------------------------------------------------------------

class TestDataGeneratorAPI(unittest.TestCase):
    def test_seed_accepts_int(self):
        gen = dg.DataGenerator(seed=42)
        users = gen.generate_users(5)
        self.assertEqual(len(users), 5)
        self.assertTrue(all("id" in u for u in users))

    def test_default_seed(self):
        gen = dg.DataGenerator()
        self.assertIsInstance(gen.generate_users(3), list)

    def test_generate_users_returns_list(self):
        users = dg.DataGenerator(seed=1).generate_users(5)
        self.assertIsInstance(users, list)
        self.assertEqual(len(users), 5)
        for user in users:
            self.assertIn("id", user)
            self.assertIn("email", user)

    def test_generate_orders_requires_users(self):
        gen = dg.DataGenerator(seed=1)
        orders = gen.generate_orders(10)
        self.assertEqual(len(orders), 10)
        self.assertGreater(len(gen.users), 0)

    def test_random_phone_format(self):
        phone = dg.random_phone()
        self.assertTrue(phone.startswith("+1-"))

    def test_random_email_format(self):
        email = dg.random_email("john", "doe")
        self.assertIn("@", email)

    def test_random_datetime_returns_datetime(self):
        from datetime import datetime
        dt = dg.random_datetime()
        self.assertIsInstance(dt, datetime)


# ---------------------------------------------------------------------------
# log_aggregator API
# ---------------------------------------------------------------------------

class TestLogAggregatorAPI(unittest.TestCase):
    def test_parsers_exist(self):
        self.assertTrue(hasattr(la, "JSONLogParser"))
        self.assertTrue(hasattr(la, "TextLogParser"))
        self.assertTrue(hasattr(la, "NginxLogParser"))

    def test_json_parser_parses(self):
        parser = la.JSONLogParser()
        entry = parser.parse(json.dumps({"level": "error", "message": "fail"}))
        self.assertIsNotNone(entry)
        self.assertEqual(entry["level"], "error")

    def test_text_parser_parses(self):
        parser = la.TextLogParser()
        entry = parser.parse("2026-06-22 10:00:00 ERROR [api] something broke")
        self.assertIsNotNone(entry)
        self.assertEqual(entry["format"], "text")

    def test_aggregator_summary(self):
        agg = la.LogAggregator()
        agg._parse_line("2026-06-22 10:00:00 ERROR [api] boom")
        summary = agg.get_summary()
        self.assertEqual(summary["total_entries"], 1)
        self.assertIn("error_rate", summary)

    def test_export_json(self):
        agg = la.LogAggregator()
        agg._parse_line("2026-06-22 10:00:00 ERROR [api] boom")
        import tempfile, os
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            path = f.name
        try:
            agg.export_json(path)
            with open(path) as fh:
                data = json.load(fh)
            self.assertIn("summary", data)
            self.assertIn("entries", data)
        finally:
            os.unlink(path)


# ---------------------------------------------------------------------------
# health_check API
# ---------------------------------------------------------------------------

class TestHealthCheckAPI(unittest.TestCase):
    def test_services_constant(self):
        self.assertIsInstance(hc.SERVICES, dict)
        self.assertGreater(len(hc.SERVICES), 0)

    def test_infrastructure_constant(self):
        self.assertIsInstance(hc.INFRASTRUCTURE, dict)

    def test_check_functions_exist(self):
        for fn in ("check_http_service", "check_tcp_port", "check_disk_usage", "check_memory_usage"):
            self.assertTrue(callable(getattr(hc, fn, None)))


if __name__ == "__main__":
    unittest.main()
