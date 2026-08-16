#!/usr/bin/env python
"""GATE 4b — the labels actually describe the audio.

  * exact (tonic AND mode) on the 300-track sample is >= 45% and <= 92%
  * `relative` is the largest non-exact bucket
  * no single non-exact bucket exceeds the exact rate
  * the negative control (shuffled labels) scores <= 15% exact
  * the full table prints regardless of pass or fail

On failure, do NOT loosen these thresholds — report the observed distribution
and stop. Near-chance exact with flat errors means a broken join; one dominant
non-exact bucket means a constant transposition.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from tonic_trainer.scoring import EXACT, NON_EXACT, RELATIVE  # noqa: E402
from tonic_trainer.validation import VALIDATION_JSON, print_summary  # noqa: E402

MIN_EXACT = 0.45
MAX_EXACT = 0.92
MAX_CONTROL_EXACT = 0.15
FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}{(' — ' + detail) if detail else ''}")
    if not ok:
        FAILURES.append(name)


def main() -> int:
    print("=== GATE 4b — audio/label pairing ===")
    if not VALIDATION_JSON.exists():
        print(f"GATE 4b FAILED: {VALIDATION_JSON} missing — run validation first")
        return 1

    payload = json.loads(VALIDATION_JSON.read_text())
    real = payload["sample"]
    control = payload["negative_control"]

    # The table prints first, pass or fail — the shape is the diagnosis.
    print_summary("real pairing, tier1+tier2", real)
    print_summary("negative control (labels shuffled)", control)
    print()

    check("sample size is 300", real["n"] == 300, str(real["n"]))
    check(f"exact rate >= {MIN_EXACT:.0%}", real["exact_rate"] >= MIN_EXACT, f"{real['exact_rate']:.1%}")
    check(f"exact rate <= {MAX_EXACT:.0%}", real["exact_rate"] <= MAX_EXACT, f"{real['exact_rate']:.1%}")

    non_exact = {b: real["buckets"][b] for b in NON_EXACT}
    largest = max(non_exact, key=lambda b: non_exact[b])
    check("`relative` is the largest non-exact bucket", largest == RELATIVE,
          f"largest is {largest} ({non_exact[largest]}) vs relative ({non_exact[RELATIVE]})")

    over = [b for b, n in non_exact.items() if n > real["buckets"][EXACT]]
    check("no non-exact bucket exceeds the exact count", not over, str(over))

    check(f"negative control exact <= {MAX_CONTROL_EXACT:.0%}",
          control["exact_rate"] <= MAX_CONTROL_EXACT, f"{control['exact_rate']:.1%}")

    print()
    print("by genre (exact = tonic+mode):")
    for genre, s in sorted(payload["by_genre"].items(), key=lambda kv: -kv[1]["n"]):
        flag = "  <-- near chance, consider demoting" if s["n"] >= 20 and s["exact_rate"] < 0.15 else ""
        print(f"  {genre:<22} n={s['n']:<4} tonic {s['tonic_only_rate']:>6.1%}  exact {s['exact_rate']:>6.1%}{flag}")

    print()
    if FAILURES:
        print(f"GATE 4b FAILED: {len(FAILURES)} check(s) failed: {', '.join(FAILURES)}")
        print("Do not loosen the thresholds. Near-chance exact with flat errors = broken join;")
        print("one dominant non-exact bucket = constant transposition in normalization.")
        return 1
    print("GATE 4b PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
