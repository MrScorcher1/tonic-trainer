#!/usr/bin/env python
"""GATE 2 — the metadata join yields enough usable, licensed, attributable tracks.

  * >= 1000 usable (annotated + licensed + attributable) tracks
  * the licensed count and the genre breakdown are printed
  * no row that survives the filter carries a NoDerivatives marker
  * every surviving row can be attributed (non-empty title and artist)
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from tonic_trainer.filter import has_nd_marker  # noqa: E402
from tonic_trainer.phase2 import JOINED_PARQUET  # noqa: E402

MIN_USABLE = 1000
FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}{(' — ' + detail) if detail else ''}")
    if not ok:
        FAILURES.append(name)


def main() -> int:
    print("=== GATE 2 — metadata join, license and coverage ===")
    if not JOINED_PARQUET.exists():
        print(f"GATE 2 FAILED: {JOINED_PARQUET} missing — run phase2 first")
        return 1

    df = pd.read_parquet(JOINED_PARQUET)
    usable = df[df["usable"]]

    check(f"usable tracks >= {MIN_USABLE}", len(usable) >= MIN_USABLE, str(len(usable)))
    check("every annotated track has a metadata row", len(df) == 5489, str(len(df)))

    nd_survivors = [lic for lic in usable["license"].unique() if has_nd_marker(lic)]
    check("no NoDerivatives license survives the filter", not nd_survivors, str(nd_survivors[:3]))

    unknown = usable[usable["license_canonical"] == "UNKNOWN"]
    check("no unclassified license survives the filter", len(unknown) == 0, str(len(unknown)))

    blank_title = usable["title"].isna() | (usable["title"].astype(str).str.strip() == "")
    blank_artist = usable["artist"].isna() | (usable["artist"].astype(str).str.strip() == "")
    check("every usable track is attributable", not (blank_title | blank_artist).any(),
          f"{int((blank_title | blank_artist).sum())} without title/artist")

    print()
    print(f"licensed count : {int(df['license_allowed'].sum())}")
    print(f"usable count   : {len(usable)}  (NC-flagged: {int(usable['license_is_nc'].sum())})")
    print()
    print("genre breakdown (usable):")
    for genre, n in usable["genre_top"].value_counts(dropna=False).items():
        label = "(untagged)" if pd.isna(genre) else str(genre)
        print(f"  {label:<22} {n:>5}")

    tier1_genres = {"Rock", "Folk", "Pop", "Blues", "Country"}
    t1 = usable[(usable["genre_top"].isin(tier1_genres)) & (usable["mode"] == "major")]
    print()
    print(f"forward look — tier1 candidates before the audio join: {len(t1)}")

    print()
    if FAILURES:
        print(f"GATE 2 FAILED: {len(FAILURES)} check(s) failed: {', '.join(FAILURES)}")
        return 1
    print("GATE 2 PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
