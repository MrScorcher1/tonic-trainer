#!/usr/bin/env python
"""GATE 4 — the puzzle manifest is valid, attributable, licensed and balanced.

  * validates against tests/manifest.schema.json
  * every audio_path resolves to a real file
  * every entry has non-empty title, artist, license
  * no entry carries a NoDerivatives license
  * tier1 has >= 200 entries
  * the served pool's key distribution is printed; no key exceeds 25%

On the ND check: the spec words it as "no license string containing ND
(case-insensitive)". Taken as a bare substring that also matches Netherlands,
England, Finland, Switzerland and Sound Recording — 20 innocent license strings
in this corpus — so it is enforced two ways, both strictly: no NoDerivatives
marker in any spelling, and no standalone `ND` token.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import jsonschema  # noqa: E402

from tonic_trainer.clips import CLIP_ROOT  # noqa: E402
from tonic_trainer.filter import has_nd_marker  # noqa: E402
from tonic_trainer.manifest import (  # noqa: E402
    MANIFEST_JSON,
    MAX_KEY_SHARE,
    key_distribution,
    served_pool,
)

SCHEMA = ROOT / "tests" / "manifest.schema.json"
MIN_TIER1 = 200
FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}{(' — ' + detail) if detail else ''}")
    if not ok:
        FAILURES.append(name)


def main() -> int:
    print("=== GATE 4 — puzzle manifest ===")
    if not MANIFEST_JSON.exists():
        print(f"GATE 4 FAILED: {MANIFEST_JSON} missing")
        return 1

    entries = json.loads(MANIFEST_JSON.read_text())
    schema = json.loads(SCHEMA.read_text())

    try:
        jsonschema.validate(entries, schema)
        check("manifest validates against tests/manifest.schema.json", True, f"{len(entries)} entries")
    except jsonschema.ValidationError as exc:
        check("manifest validates against tests/manifest.schema.json", False,
              f"{exc.message} at {list(exc.absolute_path)[:4]}")

    missing = [e["id"] for e in entries if not (CLIP_ROOT / e["audio_path"]).exists()]
    check("every audio_path resolves to a real file", not missing, f"{len(missing)} missing: {missing[:5]}")

    blank = [e["id"] for e in entries
             if not e["title"].strip() or not e["artist"].strip() or not e["license"].strip()]
    check("every entry has title, artist and license", not blank, f"{len(blank)}: {blank[:5]}")

    nd = [e["id"] for e in entries if has_nd_marker(e["license"])]
    check("no entry carries a NoDerivatives license", not nd, f"{len(nd)}: {nd[:5]}")

    nd_token = [e["id"] for e in entries if re.search(r"\bnd\b", e["license"], re.IGNORECASE)]
    check("no license string contains a standalone 'ND' token", not nd_token, f"{len(nd_token)}")

    tiers: dict[str, int] = {}
    for e in entries:
        tiers[e["difficulty"]] = tiers.get(e["difficulty"], 0) + 1
    check(f"tier1 has >= {MIN_TIER1} entries", tiers.get("tier1", 0) >= MIN_TIER1, str(tiers.get("tier1", 0)))

    ids = [e["id"] for e in entries]
    check("puzzle ids are unique", len(set(ids)) == len(ids), f"{len(ids) - len(set(ids))} duplicates")

    pool = served_pool(entries)
    dist = key_distribution(pool)
    top_share = dist.iloc[0] / len(pool)
    check(f"no single key exceeds {MAX_KEY_SHARE:.0%} of the served pool",
          top_share <= MAX_KEY_SHARE, f"{dist.index[0]} at {top_share:.1%}")

    print()
    print("tier counts:")
    for tier in ("tier1", "tier2", "tier3", "untagged"):
        print(f"  {tier:<10} {tiers.get(tier, 0)}")
    print()
    print(f"served pool (tier1+tier2+tier3): {len(pool)}")
    print("key distribution across the served pool:")
    for label, n in dist.items():
        print(f"  {label:<12} {n:>5}  {n / len(pool):6.1%}")

    print()
    if FAILURES:
        print(f"GATE 4 FAILED: {len(FAILURES)} check(s) failed: {', '.join(FAILURES)}")
        return 1
    print("GATE 4 PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
