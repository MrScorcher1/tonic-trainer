#!/usr/bin/env python
"""GATE S1 — the static split hides the answers and still round-trips.

  * manifest.json matches the served-pool count and contains no answer field,
    asserted on the RAW JSON STRING (a structural check would miss a leak
    through an unexpected key)
  * exactly one answer file per entry, every id resolving
  * no answer file contains a readable key name — only the hash
  * round-trip: brute-forcing the 24 combinations against a stored hash recovers
    exactly the answer the source manifest holds, for 50 random puzzles. This is
    the S-phase equivalent of Gate 4b's join check: a mis-keyed split would score
    every guess wrong, silently.
  * salting works: two puzzles sharing a key have DIFFERENT hashes. Equal hashes
    mean the id was left out of the hash input, which collapses all 3,094 files
    into a 24-entry lookup table for the whole corpus.
"""

from __future__ import annotations

import json
import random
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from tonic_trainer.manifest import load_manifest, published_entries  # noqa: E402
from tonic_trainer.static_build import (  # noqa: E402
    ANSWER_FIELDS,
    ANSWERS_DIR,
    STATIC_MANIFEST,
    verifier,
)

# Two different jobs, two different regexes, on purpose.
#
# ANSWER FILES get the spec's literal rule: /[A-G][#b]?\s*(major|minor)/i. Those
# files contain nothing but a hash, so the loosest possible pattern is free.
KEY_NAME_IN_ANSWER = re.compile(r"[A-G][#b]?\s*(major|minor)", re.IGNORECASE)
#
# THE MANIFEST carries human attribution text, where that loose pattern matches
# prose: "The Minor Thirds" (a band) hits on the "e" of "The". The rule that
# actually describes a leaked key needs a word boundary before the note letter —
# it drops "Prelude In D Minor", which really is in D minor, and keeps the band.
# This is the same regex the manifest builder filters on, so gate and filter
# cannot disagree.
KEY_NAME_IN_TEXT = re.compile(r"\b[A-G][#b]?\s+(major|minor)\b", re.IGNORECASE)
MODES = ("major", "minor")
SAMPLE = 50
FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}{(' — ' + detail) if detail else ''}")
    if not ok:
        FAILURES.append(name)


def brute_force(puzzle_id: str, stored_hash: str) -> tuple[int, str] | None:
    """Recover (tonic_pc, mode) the way the browser does: 24 hashes, one match."""
    for pc in range(12):
        for mode in MODES:
            if verifier(puzzle_id, pc, mode) == stored_hash:
                return pc, mode
    return None


def main() -> int:
    print("=== GATE S1 — static manifest split ===")
    if not STATIC_MANIFEST.exists():
        print(f"GATE S1 FAILED: {STATIC_MANIFEST} missing — run static_build first")
        return 1

    # The PUBLISHED pool, not the raw manifest. Checking against the manifest is
    # what let the site ship 227 puzzles whose audio was never uploaded: the
    # count matched perfectly and every one of them 404ed on selection.
    source = published_entries()
    raw = STATIC_MANIFEST.read_text()
    public = json.loads(raw)

    check("manifest entry count equals the published pool", len(public) == len(source),
          f"{len(public)} vs {len(source)}")

    # The check that would have caught the 404s. Count equality does not imply
    # set equality, and it is the audio_path that has to exist on the CDN.
    publishable = {e["audio_path"] for e in source}
    orphans = [e["audio_path"] for e in public if e["audio_path"] not in publishable]
    check("every site-manifest audio_path is in the published set", not orphans,
          f"{len(orphans)} would 404, first: {orphans[:5]}")

    published_ids = {e["id"] for e in source}
    unserved = [e["id"] for e in source if e["id"] not in {p["id"] for p in public}]
    check("every published clip is reachable from the site manifest", not unserved,
          f"{len(unserved)}: {unserved[:5]}")
    check("no site-manifest id is absent from the published pool",
          all(e["id"] in published_ids for e in public))

    leaked = [f for f in ANSWER_FIELDS if f in raw]
    check("manifest raw JSON contains no answer field", not leaked, str(leaked))
    hit = KEY_NAME_IN_TEXT.search(raw)
    check("manifest raw JSON contains no key name", not hit, hit.group(0) if hit else "")

    files = {p.stem: p for p in ANSWERS_DIR.glob("*.json")}
    check("exactly one answer file per entry", len(files) == len(public),
          f"{len(files)} files vs {len(public)} entries")
    missing = [e["id"] for e in public if e["id"] not in files]
    check("every manifest id resolves to an answer file", not missing,
          f"{len(missing)}: {missing[:5]}")

    bad_content: list[str] = []
    for pid, path in files.items():
        text = path.read_text()
        if KEY_NAME_IN_ANSWER.search(text) or any(f in text for f in ANSWER_FIELDS):
            bad_content.append(pid)
            continue
        payload = json.loads(text)
        if set(payload) != {"h"} or not re.fullmatch(r"[0-9a-f]{64}", payload["h"]):
            bad_content.append(pid)
    check("no answer file contains a readable key name or anything but the hash",
          not bad_content, f"{len(bad_content)}: {bad_content[:5]}")

    by_id = {e["id"]: e for e in load_manifest()}
    rng = random.Random(4242)
    sample = rng.sample(list(files), min(SAMPLE, len(files)))
    check(f"there are at least {SAMPLE} answer files to sample",
          len(sample) == SAMPLE, f"{len(sample)} available")

    mismatches = []
    for pid in sample:
        stored = json.loads(files[pid].read_text())["h"]
        recovered = brute_force(pid, stored)
        expected = (by_id[pid]["tonic_pc"], by_id[pid]["mode"])
        if recovered != expected:
            mismatches.append((pid, recovered, expected))
    check(f"brute-force round-trip recovers the source answer for {len(sample)} puzzles",
          not mismatches, str(mismatches[:3]))

    # Salting: same key, different id -> different hash.
    by_key: dict[tuple[int, str], list[str]] = defaultdict(list)
    for e in source:
        by_key[(e["tonic_pc"], e["mode"])].append(e["id"])
    shared = next((ids for ids in by_key.values() if len(ids) >= 2), None)
    if shared is None:
        check("a key shared by two puzzles exists to test salting", False)
    else:
        a, b = shared[0], shared[1]
        ha = json.loads(files[a].read_text())["h"]
        hb = json.loads(files[b].read_text())["h"]
        check("two puzzles with the same key have different hashes", ha != hb,
              f"{a} and {b} share a key; hashes {'differ' if ha != hb else 'MATCH — id is not in the hash input'}")

    all_hashes = [json.loads(p.read_text())["h"] for p in files.values()]
    check("hashes are unique across the corpus",
          len(set(all_hashes)) == len(all_hashes),
          f"{len(all_hashes) - len(set(all_hashes))} duplicates")

    print()
    print(f"manifest : {len(public)} entries, {len(raw) / 1e6:.2f} MB uncompressed")
    print(f"answers  : {len(files)} verifier files")

    print()
    if FAILURES:
        print(f"GATE S1 FAILED: {len(FAILURES)} check(s) failed: {', '.join(FAILURES)}")
        return 1
    print("GATE S1 PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
