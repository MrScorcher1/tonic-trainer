#!/usr/bin/env python
"""GATES S3 and S4 — the static build behaves, and keeps its promises.

S3: 50 draws return >= 40 distinct puzzles (carried over from Gate 5, now that
selection happens in the browser).

S4: every Gate 6 invariant still holds on the static build, plus the three
assertions that only exist here —
  * the manifest response body carries no answer field (network level),
  * no answers/*.json is requested before submission, which is what makes
    "peeking requires intent" testable rather than asserted,
  * a failed audio fetch shows a clear message naming the likely cause.

Run against the same docs/ tree GitHub Pages publishes, served by the stdlib
http.server so no custom code can drift from the deployed artifact.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "gates"))

from _playwright import browser_env, run_suite  # noqa: E402

FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}{(' — ' + detail) if detail else ''}")
    if not ok:
        FAILURES.append(name)


def main() -> int:
    print("=== GATES S3 + S4 — static build ===")

    for name in ("index.html", "app.js", "scoring.js", "style.css", "manifest.json", "config.json"):
        check(f"docs/{name} exists", (ROOT / "docs" / name).exists())
    answers = ROOT / "docs" / "answers"
    check("docs/answers/ is populated", answers.is_dir() and any(answers.glob("*.json")),
          f"{len(list(answers.glob('*.json'))) if answers.is_dir() else 0} files")

    _env, build = browser_env()
    check("headless WebKit is installed", build is not None, str(build))
    if build is None:
        print("\nGATE S4 FAILED: the browser this gate requires is not available.")
        return 1

    rc, output = run_suite([], quiet_tail=6000)
    print(output)
    check("the whole suite passes on the static build at both viewports", rc == 0)

    print()
    if FAILURES:
        print(f"GATES S3+S4 FAILED: {len(FAILURES)} check(s) failed: {', '.join(FAILURES)}")
        return 1
    print("GATES S3 + S4 PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
