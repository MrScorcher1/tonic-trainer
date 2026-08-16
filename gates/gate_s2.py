#!/usr/bin/env python
"""GATE S2 — the scoring ported to JS reproduces the server's verdicts.

The `relative_error` taxonomy is what the app teaches, so this is a port, not a
rewrite: the JS suite runs the SAME fixture table as the Python
`test_relative_error_taxonomy` parametrisation, case for case. A behaviour
change here is a regression.

Also verified here because it is the same trust boundary: the verifier round-trip
(a hash recovers exactly the answer it stands for) and the salt (a verifier made
under a different puzzle id does not resolve).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "gates"))

from _playwright import run_suite  # noqa: E402

FAILURES: list[str] = []
PY_FIXTURES = ROOT / "tests" / "test_server.py"
JS_FIXTURES = ROOT / "tests" / "e2e" / "scoring.spec.js"


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}{(' — ' + detail) if detail else ''}")
    if not ok:
        FAILURES.append(name)


def parse_python_cases() -> set[tuple]:
    """The parametrised taxonomy cases in the Python suite."""
    text = PY_FIXTURES.read_text()
    block = re.search(r"test_relative_error_taxonomy.*?\)\n", text, re.DOTALL)
    source = text[: block.end()] if block else text
    return set(re.findall(
        r'\((\d+),\s*"(major|minor)",\s*(\d+),\s*"(major|minor)",\s*"(\w+)"\)', source))


def parse_js_cases() -> set[tuple]:
    return set(re.findall(
        r'\[(\d+),\s*"(major|minor)",\s*(\d+),\s*"(major|minor)",\s*"(\w+)"\]',
        JS_FIXTURES.read_text()))


def main() -> int:
    print("=== GATE S2 — client-side scoring ===")

    py, js = parse_python_cases(), parse_js_cases()
    check("the JS fixture table is the Python one, case for case", py == js,
          f"python-only {sorted(py - js)[:3]} | js-only {sorted(js - py)[:3]}")
    check("every bucket is represented in the fixtures",
          {c[4] for c in js} == {"exact", "relative", "parallel", "semitone", "fifth", "other"},
          str(sorted({c[4] for c in js})))

    rc, output = run_suite(["tests/e2e/scoring.spec.js"])
    print(output)
    check("the ported scoring suite passes at both viewports", rc == 0)

    print()
    if FAILURES:
        print(f"GATE S2 FAILED: {len(FAILURES)} check(s) failed: {', '.join(FAILURES)}")
        return 1
    print("GATE S2 PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
