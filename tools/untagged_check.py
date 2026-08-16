"""Does the untagged pool deserve to be excluded by default?

SPEC Phase 4b anticipates exactly this test: "Exact >= 55% overall but one genre
near chance -> that genre is tonally ambiguous, not broken." The untagged pool is
~1,900 puzzles that no one currently sees. If its estimator agreement sits near
chance (~4% for tonic+mode, ~8% for tonic-only) the default exclusion is
empirically justified; if it is close to tier1, the default is hiding usable
material.

Census, not a sample — same instrument as Gate 4b, so the numbers compare
directly. This only measures; changing the serving pool is not its call.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tonic_trainer.manifest import load_manifest  # noqa: E402
from tonic_trainer.paths import BUILD  # noqa: E402
from tonic_trainer.scoring import BUCKETS, classify  # noqa: E402
from tonic_trainer.validation import _analyse  # noqa: E402

OUT = BUILD / "untagged_check.json"


def measure(name: str, entries: list[dict], workers: int = 12) -> dict:
    print(f"\nanalysing {len(entries)} {name} clips ...", flush=True)
    buckets: Counter[str] = Counter()
    tonic = 0
    failed: list[tuple[str, str]] = []
    preds: dict[str, tuple[int, str]] = {}
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for i, (pid, pc, mode) in enumerate(pool.map(_analyse, entries, chunksize=4), start=1):
            if pc is None:
                failed.append((pid, mode))
            else:
                preds[pid] = (pc, mode)
            if i % 400 == 0:
                print(f"  {i}/{len(entries)}", flush=True)

    for e in entries:
        if e["id"] not in preds:
            continue
        pc, mode = preds[e["id"]]
        buckets[classify(pc, mode, e["tonic_pc"], e["mode"])] += 1
        tonic += pc == e["tonic_pc"]

    n = len(preds)
    summary = {
        "pool": name, "n": n, "unanalysable": len(failed),
        "exact": buckets["exact"] / n if n else 0.0,
        "tonic_only": tonic / n if n else 0.0,
        "buckets": {b: buckets[b] / n if n else 0.0 for b in BUCKETS},
        "counts": {b: buckets[b] for b in BUCKETS},
    }
    print(f"  {name}: n={n}, exact {summary['exact']:.1%}, tonic-only {summary['tonic_only']:.1%}"
          + (f", {len(failed)} unanalysable" if failed else ""))
    if failed:
        print(f"  unanalysable examples: {failed[:3]}")
    return summary


def main() -> None:
    entries = load_manifest()
    untagged = [e for e in entries if e["difficulty"] == "untagged"]
    tier12 = [e for e in entries if e["difficulty"] in ("tier1", "tier2")]
    tier3 = [e for e in entries if e["difficulty"] == "tier3"]

    results = [measure("untagged", untagged), measure("tier3", tier3), measure("tier1+tier2", tier12)]
    OUT.write_text(json.dumps(results, indent=1))

    print()
    print(f"{'pool':<14} {'n':>5} {'exact':>8} {'tonic-only':>11}")
    for r in results:
        print(f"{r['pool']:<14} {r['n']:>5} {r['exact']:>7.1%} {r['tonic_only']:>10.1%}")
    print()
    print("chance under an independent pairing: ~4.2% tonic+mode, ~8.3% tonic-only")
    print(f"written: {OUT}")


if __name__ == "__main__":
    main()
