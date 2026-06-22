#!/usr/bin/env python3
"""
Diagnostic metadata diff tool.

Compares two diagnostic build JSON artifacts (diagnostic/build-*.json)
and reports differences in build metadata and per-module status. Useful
for tracking how a build's outcome changes between commits, environments,
or toolchain upgrades.

Usage:
    python3 tools/diagnostic_diff.py old.json new.json
    python3 tools/diagnostic_diff.py old.json new.json --json
    python3 tools/diagnostic_diff.py old.json new.json --output diff.json
"""

import argparse
import json
import sys
from typing import Any, Dict, List, Optional


# Top-level fields compared for value changes (everything except large
# free-text fields that are better reported at module granularity).
META_FIELDS = (
    "generated_at",
    "commit",
    "diagnostic_logd",
    "total_modules",
    "passed",
    "failed",
    "chunked",
    "chunk_size_bytes",
    "message_blocker",
    "diagnostic_logd_error",
)

MODULE_FIELDS = ("status", "elapsed_seconds", "artifact")


def load_report(path: str) -> Dict[str, Any]:
    """Load a diagnostic build JSON report from ``path``."""
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _module_map(report: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Index a report's modules list by module name."""
    modules: Dict[str, Dict[str, Any]] = {}
    for module in report.get("modules", []):
        name = module.get("name")
        if name is not None:
            modules[name] = module
    return modules


def diff_reports(old: Dict[str, Any], new: Dict[str, Any]) -> Dict[str, Any]:
    """Compute the metadata diff between two diagnostic reports.

    Returns a dict with ``metadata_changes``, ``modules_added``,
    ``modules_removed``, and ``module_changes``.
    """
    result: Dict[str, Any] = {
        "metadata_changes": [],
        "modules_added": [],
        "modules_removed": [],
        "module_changes": [],
    }

    # Top-level metadata value changes.
    for field in META_FIELDS:
        old_val = old.get(field)
        new_val = new.get(field)
        if old_val != new_val:
            result["metadata_changes"].append(
                {"field": field, "old": old_val, "new": new_val}
            )

    old_modules = _module_map(old)
    new_modules = _module_map(new)

    result["modules_added"] = sorted(set(new_modules) - set(old_modules))
    result["modules_removed"] = sorted(set(old_modules) - set(new_modules))

    for name in sorted(set(old_modules) & set(new_modules)):
        old_mod = old_modules[name]
        new_mod = new_modules[name]
        field_changes: List[Dict[str, Any]] = []
        for field in MODULE_FIELDS:
            old_val = old_mod.get(field)
            new_val = new_mod.get(field)
            if old_val != new_val:
                field_changes.append(
                    {"field": field, "old": old_val, "new": new_val}
                )
        if field_changes:
            result["module_changes"].append(
                {"module": name, "changes": field_changes}
            )

    return result


def format_diff(diff: Dict[str, Any]) -> str:
    """Render a diff dict as human-readable text."""
    lines: List[str] = []
    lines.append("=" * 60)
    lines.append("Diagnostic Metadata Diff")
    lines.append("=" * 60)

    meta = diff.get("metadata_changes", [])
    if meta:
        lines.append("\nMetadata changes:")
        for change in meta:
            lines.append(
                f"  {change['field']}: {change['old']!r} -> {change['new']!r}"
            )
    else:
        lines.append("\nMetadata: no changes")

    added = diff.get("modules_added", [])
    if added:
        lines.append("\nModules added:")
        for name in added:
            lines.append(f"  + {name}")

    removed = diff.get("modules_removed", [])
    if removed:
        lines.append("\nModules removed:")
        for name in removed:
            lines.append(f"  - {name}")

    module_changes = diff.get("module_changes", [])
    if module_changes:
        lines.append("\nModule status changes:")
        for entry in module_changes:
            lines.append(f"  {entry['module']}:")
            for change in entry["changes"]:
                lines.append(
                    f"    {change['field']}: {change['old']!r} -> {change['new']!r}"
                )
    else:
        lines.append("\nModule status: no changes")

    lines.append("")
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Diff two diagnostic build JSON reports")
    parser.add_argument("old", help="Path to the older diagnostic JSON report")
    parser.add_argument("new", help="Path to the newer diagnostic JSON report")
    parser.add_argument("--json", "-j", action="store_true", help="Output diff as JSON")
    parser.add_argument("--output", "-o", help="Write diff to a file instead of stdout")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    old = load_report(args.old)
    new = load_report(args.new)
    diff = diff_reports(old, new)

    if args.json:
        output = json.dumps(diff, indent=2, default=str)
    else:
        output = format_diff(diff)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(output)
        print(f"Diff written to {args.output}")
    else:
        print(output)

    return 0


if __name__ == "__main__":
    main()
