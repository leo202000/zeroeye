"""
Tests for health check retry/backoff support (bounty #2).

Covers transient-failure detection, retry-then-success, retry exhaustion
with exponential backoff, non-transient failures not being retried, and
the integration of retry_with_backoff into check_http_service and
check_tcp_port.
"""

import sys
import unittest
from unittest.mock import patch, MagicMock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))

import health_check
from health_check import (
    is_transient_exception,
    retry_with_backoff,
    check_http_service,
    check_tcp_port,
)


class TestIsTransientException(unittest.TestCase):
    def test_timeout_is_transient(self):
        self.assertTrue(is_transient_exception(TimeoutError("timed out")))
        self.assertTrue(is_transient_exception(ConnectionResetError("reset by peer")))

    def test_value_error_is_not_transient(self):
        self.assertFalse(is_transient_exception(ValueError("bad value")))

    def test_message_based_detection(self):
        self.assertTrue(is_transient_exception(RuntimeError("Connection timed out")))
        self.assertFalse(is_transient_exception(RuntimeError("invalid host")))


class TestRetryWithBackoff(unittest.TestCase):
    def test_succeeds_first_try(self):
        calls = []
        def func():
            calls.append(1)
            return "ok"
        result = retry_with_backoff(func, max_attempts=3, backoff_base=0.01)
        self.assertEqual(result, "ok")
        self.assertEqual(len(calls), 1)

    def test_succeeds_after_transient_failures(self):
        attempts = []
        def func():
            attempts.append(1)
            if len(attempts) < 3:
                raise ConnectionResetError("reset by peer")
            return "recovered"
        with patch("health_check.time.sleep") as mock_sleep:
            result = retry_with_backoff(func, max_attempts=5, backoff_base=0.1, backoff_factor=2.0)
        self.assertEqual(result, "recovered")
        self.assertEqual(len(attempts), 3)
        # two sleeps between three attempts
        self.assertEqual(mock_sleep.call_count, 2)

    def test_exhausts_attempts_and_reraises(self):
        import socket
        def func():
            raise socket.timeout("timed out")
        with patch("health_check.time.sleep"):
            with self.assertRaises(socket.timeout):
                retry_with_backoff(func, max_attempts=3, backoff_base=0.01)

    def test_non_transient_not_retried(self):
        attempts = []
        def func():
            attempts.append(1)
            raise ValueError("not transient")
        with self.assertRaises(ValueError):
            retry_with_backoff(func, max_attempts=5, backoff_base=0.01)
        self.assertEqual(len(attempts), 1)

    def test_backoff_is_exponential(self):
        attempts = []
        def func():
            attempts.append(1)
            if len(attempts) < 4:
                raise ConnectionResetError("reset")
            return "ok"
        with patch("health_check.time.sleep") as mock_sleep:
            retry_with_backoff(func, max_attempts=5, backoff_base=1.0, backoff_factor=2.0)
        delays = [call.args[0] for call in mock_sleep.call_args_list]
        self.assertEqual(delays, [1.0, 2.0, 4.0])


class TestCheckHttpServiceRetry(unittest.TestCase):
    def test_retries_on_transient_then_succeeds(self):
        call_count = {"n": 0}
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = b"ok"
        mock_conn = MagicMock()
        mock_conn.getresponse.return_value = mock_resp

        def fake_connection(*a, **kw):
            call_count["n"] += 1
            if call_count["n"] < 2:
                raise ConnectionResetError("reset by peer")
            return mock_conn

        with patch("http.client.HTTPConnection", side_effect=fake_connection), \
             patch("health_check.time.sleep"):
            result, detail, code = check_http_service("localhost", 8080, "/health", 5)
        self.assertEqual(result, "OK")
        self.assertEqual(code, 200)
        self.assertEqual(call_count["n"], 2)

    def test_critical_on_exhausted_retries(self):
        with patch("http.client.HTTPConnection", side_effect=ConnectionResetError("reset")), \
             patch("health_check.time.sleep"):
            result, detail, code = check_http_service("localhost", 8080, "/health", 5)
        self.assertEqual(result, "CRITICAL")
        self.assertEqual(code, 0)


class TestCheckTcpPortRetry(unittest.TestCase):
    def test_retries_then_connects(self):
        call_count = {"n": 0}
        mock_sock = MagicMock()
        def fake_connect(*a, **kw):
            call_count["n"] += 1
            if call_count["n"] < 2:
                raise ConnectionResetError("reset")
            return mock_sock
        with patch("socket.create_connection", side_effect=fake_connect), \
             patch("health_check.time.sleep"):
            result, detail, latency = check_tcp_port("localhost", 6379, 5)
        self.assertEqual(result, "OK")
        self.assertEqual(call_count["n"], 2)

    def test_connection_refused_critical(self):
        with patch("socket.create_connection", side_effect=ConnectionRefusedError()), \
             patch("health_check.time.sleep"):
            result, detail, latency = check_tcp_port("localhost", 6379, 5)
        self.assertEqual(result, "CRITICAL")
        self.assertEqual(latency, 0)

    def test_timeout_critical(self):
        import socket
        with patch("socket.create_connection", side_effect=socket.timeout("timed out")), \
             patch("health_check.time.sleep"):
            result, detail, latency = check_tcp_port("localhost", 6379, 5)
        self.assertEqual(result, "CRITICAL")
        self.assertIn("timeout", detail.lower())


def socket_timeout():
    import socket
    return socket.timeout("timed out")


if __name__ == "__main__":
    unittest.main()
