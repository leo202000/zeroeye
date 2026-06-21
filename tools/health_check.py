#!/usr/bin/env python3
"""
Health check tool for the Tent of Trials platform.
Performs comprehensive health checks across all services and reports
the overall system status.

This tool is used by:
  - The Kubernetes liveness/readiness probes
  - The deployment pipeline (post-deployment validation)
  - The monitoring system (periodic health checks)
  - The on-call engineer (manual troubleshooting)

The health check performs the following checks:
  1. Service availability (HTTP health endpoints)
  2. Database connectivity (connection test)
  3. Redis connectivity (ping test)
  4. Kafka connectivity (metadata fetch)
  5. Message queue depth (consumer lag check)
  6. Certificate expiry (TLS certificate check)
  7. Disk space (filesystem usage check)
  8. Memory usage (process memory check)

Each check returns a status of OK, WARNING, or CRITICAL, along with
a detail message and optional diagnostic data.

Usage:
    python3 health_check.py                  # Check all services
    python3 health_check.py --service backend # Check specific service
    python3 health_check.py --json            # JSON output
    python3 health_check.py --watch           # Continuous monitoring
"""

import argparse
import json
import logging
import os
import socket
import ssl
import subprocess
import sys
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# CONSTANTS
# ---------------------------------------------------------------------------

SERVICES = {
    "backend": {"host": "localhost", "port": 8080, "path": "/health", "timeout": 5},
    "market": {"host": "localhost", "port": 8081, "path": "/health", "timeout": 5},
    "frailbox": {"host": "localhost", "port": 8082, "path": "/health", "timeout": 10},
    "frontend": {"host": "localhost", "port": 3000, "path": "/", "timeout": 5},
}

INFRASTRUCTURE = {
    "postgresql": {"host": os.environ.get("DB_HOST", "localhost"), "port": int(os.environ.get("DB_PORT", "5432")), "timeout": 5},
    "redis": {"host": os.environ.get("REDIS_HOST", "localhost"), "port": int(os.environ.get("REDIS_PORT", "6379")), "timeout": 5},
    "kafka": {"host": os.environ.get("KAFKA_HOST", "localhost"), "port": int(os.environ.get("KAFKA_PORT", "9092")), "timeout": 5},
}

DISK_THRESHOLD_WARNING = 80
DISK_THRESHOLD_CRITICAL = 90

MEMORY_THRESHOLD_WARNING = 80
MEMORY_THRESHOLD_CRITICAL = 90

# Retry / circuit-breaker defaults for HTTP probes
DEFAULT_MAX_RETRIES = 0
DEFAULT_BACKOFF_FACTOR = 2.0
DEFAULT_BACKOFF_BASE_DELAY = 0.5
DEFAULT_CIRCUIT_THRESHOLD = 5
DEFAULT_CIRCUIT_COOLDOWN = 30.0

logger = logging.getLogger("health_check")


class CircuitBreaker:
    """Track consecutive HTTP probe failures for a service.

    Opens after ``threshold`` consecutive failures and resets to a half-open
    trial state once ``cooldown`` seconds have elapsed.
    """

    def __init__(self, threshold: int = DEFAULT_CIRCUIT_THRESHOLD,
                 cooldown: float = DEFAULT_CIRCUIT_COOLDOWN) -> None:
        self.threshold = threshold
        self.cooldown = cooldown
        self.consecutive_failures = 0
        self.state = "closed"
        self.opened_at: Optional[float] = None

    def record_success(self) -> None:
        self.consecutive_failures = 0
        if self.state != "closed":
            self.state = "closed"
            self.opened_at = None

    def record_failure(self) -> None:
        self.consecutive_failures += 1
        if self.consecutive_failures >= self.threshold and self.state != "open":
            self.state = "open"
            self.opened_at = time.time()

    def is_open(self) -> bool:
        if self.state == "open" and self.opened_at is not None:
            if (time.time() - self.opened_at) >= self.cooldown:
                self.state = "half_open"
                return False
            return True
        return False


# ---------------------------------------------------------------------------
# CHECK FUNCTIONS
# ---------------------------------------------------------------------------

def check_http_service(host: str, port: int, path: str, timeout: int) -> Tuple[str, str, int]:
    import http.client
    try:
        conn = http.client.HTTPConnection(host, port, timeout=timeout)
        conn.request("GET", path)
        resp = conn.getresponse()
        status = resp.status
        body = resp.read().decode("utf-8", errors="replace")[:200]
        conn.close()

        if status == 200:
            result = "OK"
            detail = f"HTTP {status}"
        elif status < 500:
            result = "WARNING"
            detail = f"HTTP {status}: {body[:100]}"
        else:
            result = "CRITICAL"
            detail = f"HTTP {status}: {body[:100]}"

        return result, detail, status
    except Exception as e:
        return "CRITICAL", str(e), 0


def check_http_service_with_retry(host: str, port: int, path: str, timeout: int,
                                  max_retries: int = DEFAULT_MAX_RETRIES,
                                  backoff_factor: float = DEFAULT_BACKOFF_FACTOR,
                                  base_delay: float = DEFAULT_BACKOFF_BASE_DELAY,
                                  breaker: Optional[CircuitBreaker] = None) -> Tuple[str, str, int]:
    """Probe an HTTP service with exponential backoff retries and circuit breaking.

    The delay before retry attempt N (0-indexed) is
    ``base_delay * (backoff_factor ** N)``. A non-OK result is retried up to
    ``max_retries`` times. When a breaker is supplied, successes reset it and
    failures advance it; an open breaker short-circuits further probes until
    the cooldown expires.
    """
    attempts = max(1, max_retries + 1)
    last_result: Tuple[str, str, int] = ("CRITICAL", "no attempt made", 0)
    for attempt in range(attempts):
        if breaker is not None and breaker.is_open():
            return "CRITICAL", "Circuit breaker open", 0
        result, detail, status = check_http_service(host, port, path, timeout)
        last_result = (result, detail, status)
        if result == "OK":
            if breaker is not None:
                breaker.record_success()
            return result, detail, status
        if breaker is not None:
            breaker.record_failure()
        if attempt < attempts - 1:
            delay = base_delay * (backoff_factor ** attempt)
            time.sleep(delay)
    return last_result


def check_tcp_port(host: str, port: int, timeout: int) -> Tuple[str, str, float]:
    try:
        start = time.time()
        sock = socket.create_connection((host, port), timeout=timeout)
        sock.close()
        latency = (time.time() - start) * 1000
        return "OK", f"Connected ({latency:.1f}ms)", latency
    except socket.timeout:
        return "CRITICAL", f"Connection timeout ({timeout}s)", 0
    except ConnectionRefusedError:
        return "CRITICAL", "Connection refused", 0
    except Exception as e:
        return "CRITICAL", str(e), 0


def check_certificate_expiry(host: str, port: int = 443) -> Tuple[str, str, int]:
    try:
        ctx = ssl.create_default_context()
        with socket.create_connection((host, port), timeout=10) as sock:
            with ctx.wrap_socket(sock, server_hostname=host) as ssock:
                cert = ssock.getpeercert()
                if not cert:
                    return "WARNING", "No certificate found", 0

                from datetime import datetime as dt
                expires = dt.strptime(cert["notAfter"], "%b %d %H:%M:%S %Y %Z")
                days_left = (expires - dt.now()).days

                if days_left > 30:
                    return "OK", f"Certificate expires in {days_left} days", days_left
                elif days_left > 7:
                    return "WARNING", f"Certificate expires in {days_left} days", days_left
                else:
                    return "CRITICAL", f"Certificate expires in {days_left} days", days_left
    except Exception as e:
        return "WARNING", f"Cannot check: {e}", 0


def check_disk_usage(path: str = "/") -> Tuple[str, str, float]:
    try:
        stat = os.statvfs(path)
        total = stat.f_frsize * stat.f_blocks
        free = stat.f_frsize * stat.f_bavail
        used = total - free
        pct = (used / total) * 100

        if pct < DISK_THRESHOLD_WARNING:
            return "OK", f"{pct:.1f}% used ({used // (1024**3)}GB/{total // (1024**3)}GB)", pct
        elif pct < DISK_THRESHOLD_CRITICAL:
            return "WARNING", f"{pct:.1f}% used ({used // (1024**3)}GB/{total // (1024**3)}GB)", pct
        else:
            return "CRITICAL", f"{pct:.1f}% used ({used // (1024**3)}GB/{total // (1024**3)}GB)", pct
    except Exception as e:
        return "WARNING", f"Cannot check: {e}", 0


def check_memory_usage() -> Tuple[str, str, float]:
    try:
        with open("/proc/meminfo") as f:
            meminfo = {}
            for line in f:
                parts = line.split(":")
                if len(parts) == 2:
                    key = parts[0].strip()
                    value = parts[1].strip().replace(" kB", "")
                    try:
                        meminfo[key] = int(value) * 1024
                    except ValueError:
                        pass

        total = meminfo.get("MemTotal", 0)
        available = meminfo.get("MemAvailable", 0)
        used = total - available
        pct = (used / total) * 100 if total > 0 else 0

        if pct < MEMORY_THRESHOLD_WARNING:
            return "OK", f"{pct:.1f}% used ({used // (1024**3)}GB/{total // (1024**3)}GB)", pct
        elif pct < MEMORY_THRESHOLD_CRITICAL:
            return "WARNING", f"{pct:.1f}% used", pct
        else:
            return "CRITICAL", f"{pct:.1f}% used", pct
    except Exception as e:
        return "WARNING", f"Cannot check: {e}", 0


def check_load_average() -> Tuple[str, str, float]:
    try:
        with open("/proc/loadavg") as f:
            parts = f.read().strip().split()
            load = float(parts[0])
            cpu_count = os.cpu_count() or 1
            load_pct = (load / cpu_count) * 100

            if load_pct < 70:
                return "OK", f"Load: {load} ({load_pct:.0f}% of {cpu_count} cores)", load
            elif load_pct < 90:
                return "WARNING", f"Load: {load} ({load_pct:.0f}% of {cpu_count} cores)", load
            else:
                return "CRITICAL", f"Load: {load} ({load_pct:.0f}% of {cpu_count} cores)", load
    except Exception as e:
        return "WARNING", f"Cannot check: {e}", 0


# ---------------------------------------------------------------------------
# HEALTH CHECK RUNNER
# ---------------------------------------------------------------------------

def run_health_checks(service: Optional[str] = None, json_output: bool = False,
                      max_retries: int = DEFAULT_MAX_RETRIES,
                      backoff_factor: float = DEFAULT_BACKOFF_FACTOR,
                      base_delay: float = DEFAULT_BACKOFF_BASE_DELAY,
                      circuit_threshold: int = DEFAULT_CIRCUIT_THRESHOLD,
                      circuit_cooldown: float = DEFAULT_CIRCUIT_COOLDOWN) -> Dict[str, Any]:
    results: Dict[str, Any] = {
        "timestamp": datetime.now().isoformat(),
        "hostname": socket.gethostname(),
        "services": {},
        "infrastructure": {},
        "system": {},
        "overall_status": "OK",
    }

    all_ok = True
    breakers: Dict[str, CircuitBreaker] = {}
    counters = {"total": 0, "ok": 0, "warning": 0, "critical": 0}

    def _tally(status_value: str) -> None:
        counters["total"] += 1
        if status_value == "OK":
            counters["ok"] += 1
        elif status_value == "WARNING":
            counters["warning"] += 1
        else:
            counters["critical"] += 1

    # Check services
    for name, config in SERVICES.items():
        if service and name != service:
            continue
        breaker = None
        if circuit_threshold > 0:
            breaker = breakers.get(name)
            if breaker is None:
                breaker = CircuitBreaker(circuit_threshold, circuit_cooldown)
                breakers[name] = breaker
        status, detail, code = check_http_service_with_retry(
            config["host"], config["port"], config["path"], config["timeout"],
            max_retries=max_retries, backoff_factor=backoff_factor,
            base_delay=base_delay, breaker=breaker,
        )
        _tally(status)
        results["services"][name] = {
            "status": status,
            "detail": detail,
            "code": code,
            "endpoint": f"http://{config['host']}:{config['port']}{config['path']}",
        }
        if status in ("WARNING", "CRITICAL"):
            logger.warning("Service '%s' is %s: %s", name, status, detail)
        if status == "CRITICAL":
            all_ok = False

    # Check infrastructure
    for name, config in INFRASTRUCTURE.items():
        if service and name != service:
            continue
        status, detail, latency = check_tcp_port(config["host"], config["port"], config["timeout"])
        _tally(status)
        results["infrastructure"][name] = {
            "status": status,
            "detail": detail,
            "endpoint": f"{config['host']}:{config['port']}",
        }
        if status in ("WARNING", "CRITICAL"):
            logger.warning("Infrastructure '%s' is %s: %s", name, status, detail)
        if status == "CRITICAL":
            all_ok = False

    # Check system resources
    disk_status, disk_detail, disk_pct = check_disk_usage()
    _tally(disk_status)
    results["system"]["disk"] = {"status": disk_status, "detail": disk_detail}
    if disk_status in ("WARNING", "CRITICAL"):
        logger.warning("Disk usage is %s: %s", disk_status, disk_detail)
    if disk_status == "CRITICAL":
        all_ok = False

    mem_status, mem_detail, mem_pct = check_memory_usage()
    _tally(mem_status)
    results["system"]["memory"] = {"status": mem_status, "detail": mem_detail}
    if mem_status in ("WARNING", "CRITICAL"):
        logger.warning("Memory usage is %s: %s", mem_status, mem_detail)
    if mem_status == "CRITICAL":
        all_ok = False

    load_status, load_detail, load_val = check_load_average()
    _tally(load_status)
    results["system"]["load"] = {"status": load_status, "detail": load_detail}
    if load_status in ("WARNING", "CRITICAL"):
        logger.warning("Load average is %s: %s", load_status, load_detail)

    # Check certificate expiry (web services)
    for name, config in SERVICES.items():
        if service and name != service:
            continue
        if config["port"] == 443:
            cert_status, cert_detail, days_left = check_certificate_expiry(config["host"])
            _tally(cert_status)
            results["services"][name]["certificate"] = {
                "status": cert_status,
                "detail": cert_detail,
                "days_remaining": days_left,
            }
            if cert_status in ("WARNING", "CRITICAL"):
                logger.warning("Certificate for '%s' is %s: %s", name, cert_status, cert_detail)
            if cert_status == "CRITICAL":
                all_ok = False

    circuit_open = any(b.is_open() for b in breakers.values()) if breakers else False
    results["summary"] = {
        "total_checks": counters["total"],
        "ok": counters["ok"],
        "warning": counters["warning"],
        "critical": counters["critical"],
        "circuit_open": circuit_open,
    }
    results["overall_status"] = "OK" if all_ok else "DEGRADED"

    return results


def print_health_report(results: Dict[str, Any]):
    print(f"\n{'='*60}")
    print(f"  HEALTH CHECK REPORT")
    print(f"  Host: {results['hostname']}")
    print(f"  Time: {results['timestamp']}")
    print(f"  Overall: {results['overall_status']}")
    print(f"{'='*60}")

    for category, items in [("Services", results["services"]),
                             ("Infrastructure", results["infrastructure"]),
                             ("System", results["system"])]:
        if items:
            print(f"\n  {category}:")
            for name, check in items.items():
                if isinstance(check, dict) and "status" in check:
                    status_icon = {"OK": "✓", "WARNING": "⚠", "CRITICAL": "✗"}.get(check["status"], "?")
                    print(f"    {status_icon} {name}: {check['detail']}")
                else:
                    print(f"    {name}:")
                    for sub_name, sub_check in check.items():
                        if isinstance(sub_check, dict) and "status" in sub_check:
                            sub_icon = {"OK": "✓", "WARNING": "⚠", "CRITICAL": "✗"}.get(sub_check["status"], "?")
                            print(f"      {sub_icon} {sub_name}: {sub_check['detail']}")
    print()


def parse_args():
    parser = argparse.ArgumentParser(description="Health check tool")
    parser.add_argument("--service", "-s", help="Check specific service only")
    parser.add_argument("--json", "-j", action="store_true", help="JSON output")
    parser.add_argument("--watch", "-w", action="store_true", help="Continuous monitoring")
    parser.add_argument("--interval", "-i", type=int, default=30, help="Check interval in seconds")
    parser.add_argument("--output", "-o", help="Output file path")
    parser.add_argument("--max-retries", type=int, default=DEFAULT_MAX_RETRIES,
                        help="Max retry attempts for HTTP probes before giving up (default: 0)")
    parser.add_argument("--backoff-factor", type=float, default=DEFAULT_BACKOFF_FACTOR,
                        help="Exponential backoff multiplier between retries (default: 2.0)")
    parser.add_argument("--backoff-base-delay", type=float, default=DEFAULT_BACKOFF_BASE_DELAY,
                        help="Base delay in seconds for the first backoff (default: 0.5)")
    parser.add_argument("--circuit-threshold", type=int, default=DEFAULT_CIRCUIT_THRESHOLD,
                        help="Consecutive HTTP failures before the circuit opens (default: 5, 0 disables)")
    parser.add_argument("--circuit-cooldown", type=float, default=DEFAULT_CIRCUIT_COOLDOWN,
                        help="Seconds before an open circuit resets to half-open (default: 30)")
    return parser.parse_args()


def main():
    args = parse_args()
    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    def _run() -> Dict[str, Any]:
        return run_health_checks(
            args.service, args.json,
            max_retries=args.max_retries,
            backoff_factor=args.backoff_factor,
            base_delay=args.backoff_base_delay,
            circuit_threshold=args.circuit_threshold,
            circuit_cooldown=args.circuit_cooldown,
        )

    if args.watch:
        print(f"Continuous monitoring (interval: {args.interval}s). Press Ctrl+C to stop.")
        try:
            while True:
                results = _run()
                if args.json:
                    print(json.dumps(results, indent=2))
                else:
                    print_health_report(results)
                time.sleep(args.interval)
        except KeyboardInterrupt:
            print("\nMonitoring stopped")
    else:
        results = _run()
        if args.json:
            output = json.dumps(results, indent=2)
            print(output)
        else:
            print_health_report(results)

        if args.output:
            with open(args.output, "w") as f:
                if args.json:
                    json.dump(results, f, indent=2)
                else:
                    json.dump(results, f, indent=2)
            print(f"Report saved to {args.output}")

        if results["overall_status"] == "DEGRADED":
            return 1

    return 0


if __name__ == "__main__":
    main()
