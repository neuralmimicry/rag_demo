#!/usr/bin/env python3
"""
Simple bundle budget checks for static frontend assets.

Default budgets target this repository's static files; additional budgets can be
provided with --budget path:max_bytes.
"""

from __future__ import annotations

import argparse
import os
from typing import Dict, List, Tuple


DEFAULT_BUDGETS: Dict[str, int] = {
    "web/static/app.js": 220_000,
    "web/static/admin.js": 55_000,
    "web/static/playground.js": 25_000,
    "web/static/styles.css": 80_000,
}


def _parse_budget_item(item: str) -> Tuple[str, int]:
    if ":" not in item:
        raise ValueError(f"Invalid --budget '{item}', expected path:max_bytes")
    path, raw = item.split(":", 1)
    path = path.strip()
    if not path:
        raise ValueError(f"Invalid --budget '{item}', empty path")
    max_bytes = int(raw.strip())
    if max_bytes <= 0:
        raise ValueError(f"Invalid --budget '{item}', max_bytes must be > 0")
    return path, max_bytes


def main() -> int:
    parser = argparse.ArgumentParser(description="Check static asset sizes against budgets.")
    parser.add_argument(
        "--budget",
        action="append",
        default=[],
        help="Additional/override budget in path:max_bytes form.",
    )
    parser.add_argument(
        "--root",
        default=".",
        help="Repository root for budget paths (default: current directory).",
    )
    args = parser.parse_args()

    budgets = dict(DEFAULT_BUDGETS)
    for item in args.budget:
        path, max_bytes = _parse_budget_item(item)
        budgets[path] = max_bytes

    failures: List[str] = []
    print("Bundle budget report")
    for rel_path, max_bytes in sorted(budgets.items()):
        abs_path = os.path.join(args.root, rel_path)
        if not os.path.exists(abs_path):
            failures.append(f"{rel_path}: missing")
            print(f"- {rel_path}: missing (budget={max_bytes} bytes)")
            continue
        size = os.path.getsize(abs_path)
        status = "ok" if size <= max_bytes else "over"
        print(f"- {rel_path}: {size} bytes (budget={max_bytes}) [{status}]")
        if size > max_bytes:
            failures.append(f"{rel_path}: {size} > {max_bytes}")

    if failures:
        print("Bundle budgets failed:")
        for item in failures:
            print(f"  - {item}")
        return 2
    print("All bundle budgets satisfied.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

