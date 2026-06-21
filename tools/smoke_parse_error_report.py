# -*- coding: utf-8 -*-
"""Smoke test for the --parse-error-report feature of log_aggregator.py.

Covers: valid JSON logs, malformed JSON logs, text logs, sanitized report
output (no raw payloads / secrets), and backward compatibility of existing
outputs when the new option is unused.
"""
import json
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from log_aggregator import LogAggregator, _sanitize_text  # noqa: E402

FAILURES = []


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}] {name}" + (f" -- {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


def make_log(tmpdir, name, lines):
    p = Path(tmpdir) / name
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(p)


def main():
    with tempfile.TemporaryDirectory() as tmp:
        log = make_log(tmp, "mixed.log", [
            '{"timestamp":1704067200,"level":"info","service":"api","message":"ok"}',  # 1 valid
            '{"bad json missing brace',                                                                    # 2 malformed
            '{"timestamp":1704067260,"level":"warn","service":"web","message":"slow"}',         # 3 valid
            '{not valid json}',                                                                            # 4 malformed
            'plain text log line [api] ERROR something happened',                                          # 5 text
        ])

        agg = LogAggregator()
        agg.process_file(log)

        errs = agg.parse_errors
        check("records exactly 2 parse errors", len(errs) == 2, f"got {len(errs)}")
        check("malformed lines are 2 and 4",
              [e["line"] for e in errs] == [2, 4],
              str([e["line"] for e in errs]))
        check("all errors tagged parser=json",
              all(e["parser"] == "json" for e in errs),
              str([e["parser"] for e in errs]))
        check("error messages contain no raw payload (bad json)",
              all("bad json" not in e["error"] and "not valid json" not in e["error"] for e in errs),
              str([e["error"] for e in errs]))
        check("file path recorded", all(e["file"] == log for e in errs))

        report_path = os.path.join(tmp, "parse_errors.json")
        agg.export_parse_error_report(report_path)
        report = json.loads(Path(report_path).read_text(encoding="utf-8"))
        check("report total_parse_errors == 2", report["total_parse_errors"] == 2, str(report.get("total_parse_errors")))
        check("report groups by file", log in report["errors_by_file"], str(list(report["errors_by_file"])))
        failures = report["errors_by_file"][log]["failures"]
        check("report failure lines are 2 and 4",
              [f["line"] for f in failures] == [2, 4],
              str([f["line"] for f in failures]))

        # Backward compatibility: existing outputs still work without the option.
        json_out = os.path.join(tmp, "report.json")
        agg.export_json(json_out)
        data = json.loads(Path(json_out).read_text(encoding="utf-8"))
        # 5 non-empty lines all parsed (3 JSON + 2 malformed-as-text + ... = 5)
        check("backward-compat entry count unchanged", data["summary"]["total_entries"] == 5,
              str(data["summary"]["total_entries"]))
        check("export_json still contains summary/entries",
              {"summary", "error_timeline", "service_breakdown", "entries"} <= set(data),
              str(set(data)))

        # Sanitization: secret-looking values are redacted.
        sanitized = _sanitize_text("token=ghp_oABCDEFGHIJKLMNOPQRSTUVWXY1234 and password=hunter2")
        check("secrets redacted by _sanitize_text",
              "ghp_oABCDEFGHIJKLMNOPQRSTUVWXY1234" not in sanitized and "hunter2" not in sanitized,
              sanitized)

        # CLI: --parse-error-report path writes a file end-to-end.
        out2 = os.path.join(tmp, "cli_report.json")
        rc = os.system(f'{sys.executable} "{HERE / "log_aggregator.py"}" --input "{log}" '
                       f'--output "{os.path.join(tmp, "cli_out.json")}" --parse-error-report "{out2}" >nul 2>&1')
        check("CLI --parse-error-report exits 0", rc == 0, str(rc))
        check("CLI wrote parse-error report", os.path.exists(out2))

    print()
    if FAILURES:
        print(f"SMOKE TEST FAILED: {len(FAILURES)} check(s) failed: {FAILURES}")
        return 1
    print("SMOKE TEST PASSED: all checks ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
