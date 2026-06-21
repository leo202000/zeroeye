#!/usr/bin/env python3
"""Unit tests for retry/backoff/circuit-breaker behavior in health_check."""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import health_check as hc


class CircuitBreakerTests(unittest.TestCase):
    def test_opens_after_threshold(self):
        cb = hc.CircuitBreaker(threshold=3, cooldown=1000.0)
        self.assertFalse(cb.is_open())
        cb.record_failure()
        cb.record_failure()
        self.assertFalse(cb.is_open())
        cb.record_failure()
        self.assertTrue(cb.is_open())

    def test_success_resets_failures(self):
        cb = hc.CircuitBreaker(threshold=3, cooldown=1000.0)
        cb.record_failure()
        cb.record_failure()
        cb.record_success()
        self.assertEqual(cb.consecutive_failures, 0)
        self.assertFalse(cb.is_open())
        cb.record_failure()
        cb.record_failure()
        self.assertFalse(cb.is_open())

    def test_resets_after_cooldown(self):
        cb = hc.CircuitBreaker(threshold=2, cooldown=10.0)
        cb.record_failure()
        cb.record_failure()
        self.assertTrue(cb.is_open())
        opened = cb.opened_at
        with mock.patch("health_check.time.time", return_value=opened + 11.0):
            self.assertFalse(cb.is_open())
        self.assertEqual(cb.state, "half_open")


class RetryBackoffTests(unittest.TestCase):
    def test_retry_until_success(self):
        calls = [("CRITICAL", "down", 0), ("OK", "up", 200)]
        with mock.patch("health_check.check_http_service", side_effect=calls) as probe, \
             mock.patch("health_check.time.sleep") as sleep:
            status, detail, code = hc.check_http_service_with_retry(
                "h", 80, "/", 5, max_retries=2, backoff_factor=2.0, base_delay=0.1)
        self.assertEqual(status, "OK")
        self.assertEqual(code, 200)
        self.assertEqual(probe.call_count, 2)
        sleep.assert_called_once_with(0.1)

    def test_backoff_delay_grows_exponentially(self):
        with mock.patch("health_check.check_http_service",
                        return_value=("CRITICAL", "down", 0)), \
             mock.patch("health_check.time.sleep") as sleep:
            hc.check_http_service_with_retry(
                "h", 80, "/", 5, max_retries=3, backoff_factor=2.0, base_delay=0.5)
        delays = [c.args[0] for c in sleep.call_args_list]
        self.assertEqual(delays, [0.5, 1.0, 2.0])

    def test_no_retry_when_ok(self):
        with mock.patch("health_check.check_http_service",
                        return_value=("OK", "up", 200)) as probe, \
             mock.patch("health_check.time.sleep") as sleep:
            status, _, _ = hc.check_http_service_with_retry("h", 80, "/", 5, max_retries=3)
        self.assertEqual(status, "OK")
        self.assertEqual(probe.call_count, 1)
        self.assertEqual(sleep.call_count, 0)

    def test_open_breaker_short_circuits(self):
        cb = hc.CircuitBreaker(threshold=1, cooldown=1000.0)
        cb.record_failure()
        self.assertTrue(cb.is_open())
        with mock.patch("health_check.check_http_service") as probe:
            status, detail, code = hc.check_http_service_with_retry(
                "h", 80, "/", 5, max_retries=2, breaker=cb)
        self.assertEqual(status, "CRITICAL")
        self.assertIn("Circuit breaker", detail)
        self.assertEqual(probe.call_count, 0)


class AggregationTests(unittest.TestCase):
    def test_summary_counts_and_warning_logged(self):
        with mock.patch("health_check.check_http_service",
                        return_value=("CRITICAL", "down", 0)) as http, \
             mock.patch("health_check.check_tcp_port",
                        return_value=("OK", "ok", 1.0)), \
             mock.patch("health_check.check_disk_usage",
                        return_value=("OK", "ok", 10.0)), \
             mock.patch("health_check.check_memory_usage",
                        return_value=("OK", "ok", 10.0)), \
             mock.patch("health_check.check_load_average",
                        return_value=("OK", "ok", 0.1)), \
             mock.patch("health_check.time.sleep"):
            with self.assertLogs("health_check", level="WARNING") as cm:
                results = hc.run_health_checks(max_retries=0, circuit_threshold=0)
        self.assertIn("summary", results)
        self.assertEqual(results["summary"]["critical"], 4)
        self.assertEqual(results["summary"]["ok"], 6)
        self.assertEqual(results["summary"]["total_checks"], 10)
        self.assertFalse(results["summary"]["circuit_open"])
        self.assertTrue(any("is CRITICAL" in m for m in cm.output))
        self.assertEqual(results["overall_status"], "DEGRADED")
        self.assertEqual(http.call_count, 4)


if __name__ == "__main__":
    unittest.main()
