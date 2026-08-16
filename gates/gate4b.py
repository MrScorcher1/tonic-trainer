#!/usr/bin/env python
"""GATE 4b — the labels actually describe the audio.

  * exact (tonic AND mode) on the 300-track sample is >= 45% and <= 92%
  * the largest non-exact bucket is `relative` OR `fifth`   [AMENDED — see below]
  * no single non-exact bucket exceeds the exact rate
  * the negative control (shuffled labels) scores <= 15% exact
  * the full table prints regardless of pass or fail

On failure, do NOT loosen these thresholds — report the observed distribution
and stop. Near-chance exact with flat errors means a broken join; one dominant
non-exact bucket means a constant transposition.

AMENDMENT (2026-08-16, approved by the user via the strategist)
---------------------------------------------------------------
The original criterion read "`relative` is the largest non-exact bucket",
resting on the spec's claim that Krumhansl-Schmuckler's "characteristic failure
is confusing a key with its relative major/minor". That premise is wrong, and
the correction is recorded here rather than applied silently. **No numeric
threshold was changed** — only the named buckets in this one shape check.

Evidence:

1. The observed run: exact 49.3% against a shuffled-label control of 4.0% — a
   12x separation — with tonic-only 59.7% against the control's 10.0% (chance
   8.3%). A broken join produces the control's numbers, not these.
2. The interval histogram (predicted minus label pitch class) spikes at 0 with
   179 of 300, then falls to 37 at +7. That is a sharp tonic spike with a
   dominant tail: neither the flat spread of an independent pairing nor the
   shifted spike of a constant transposition.
3. `fifth` was the largest non-exact bucket in ALL FIVE variants tested on the
   same 300 tracks (tools/estimator_experiment.py), so it is not an artefact of
   clip position or of the chroma front-end:

       variant               exact   tonic  relative  fifth   largest non-exact
       openings / cqt        49.3%   59.7%     4.3%  17.0%   fifth
       openings / harmonic   49.7%   60.3%     5.0%  17.3%   fifth
       openings / cens       48.3%   58.3%     3.0%  19.3%   fifth
       middles  / cqt        44.3%   54.0%     7.3%  18.7%   fifth
       middles  / harmonic   44.0%   52.7%     6.7%  20.0%   fifth

4. MIREX's Audio Key Detection score treats a perfect-fifth error as the most
   forgivable near-miss of all: correct 1.0, **fifth 0.5**, relative 0.3,
   parallel 0.2. The field's own benchmark rates a fifth error as closer than a
   relative error — the spec's premise was backwards, not merely incomplete.

A key and its dominant share six of seven scale degrees, so the rotated-profile
correlation for the fifth sits just under the tonic's. The gate's actual job —
telling a sane join from an independent one — is done by the exact rate against
the negative control, and that discrimination is intact.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from tonic_trainer.scoring import EXACT, FIFTH, NON_EXACT, RELATIVE  # noqa: E402
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
    # AMENDED criterion — see the module docstring for the evidence. Both are
    # near-key confusions; MIREX scores fifth 0.5 and relative 0.3.
    check("the largest non-exact bucket is `relative` or `fifth`",
          largest in (RELATIVE, FIFTH),
          f"largest is {largest} ({non_exact[largest]}); "
          f"relative {non_exact[RELATIVE]}, fifth {non_exact[FIFTH]}")

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
