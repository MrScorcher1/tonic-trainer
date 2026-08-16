#!/usr/bin/env python
"""GATE 1 — the normalized key table matches the source's verified facts.

Checks (SPEC Gate 1):
  * exactly 5489 rows
  * tonic_pc integer in [0, 11], non-null, every row
  * exactly 2 distinct modes and 24 distinct (tonic_pc, mode) pairs
  * exactly 232 null spotify_uri
  * 100% round-trip from (tonic_pc, mode) back to the source's own spelling
  * unit tests pass, including Bb->10 / D#->3
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from tonic_trainer.normalize import source_label  # noqa: E402
from tonic_trainer.phase1 import KEYS_PARQUET  # noqa: E402

FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}{(' — ' + detail) if detail else ''}")
    if not ok:
        FAILURES.append(name)


def main() -> int:
    print("=== GATE 1 — key ingest and normalization ===")

    if not KEYS_PARQUET.exists():
        print(f"GATE 1 FAILED: {KEYS_PARQUET} does not exist — run phase1 first")
        return 1

    df = pd.read_parquet(KEYS_PARQUET)

    check("row count == 5489", len(df) == 5489, str(len(df)))

    pc = df["tonic_pc"]
    check("tonic_pc non-null", not pc.isna().any(), f"{int(pc.isna().sum())} nulls")
    check("tonic_pc integer dtype", pd.api.types.is_integer_dtype(pc), str(pc.dtype))
    check("tonic_pc in [0, 11]", bool(pc.between(0, 11).all()), f"min {pc.min()} max {pc.max()}")

    modes = sorted(df["mode"].unique())
    check("exactly 2 modes", len(modes) == 2, str(modes))

    pairs = df.groupby(["tonic_pc", "mode"]).ngroups
    check("24 distinct (tonic_pc, mode) pairs", pairs == 24, str(pairs))

    nulls = int(df["spotify_uri"].isna().sum())
    check("null spotify_uri == 232", nulls == 232, str(nulls))

    rebuilt = [source_label(p, m) for p, m in zip(df["tonic_pc"], df["mode"], strict=True)]
    matches = int((pd.Series(rebuilt, index=df.index) == df["key_label_original"]).sum())
    check("round-trip reproduces source spelling for 100% of rows", matches == len(df), f"{matches}/{len(df)}")

    ids = df["track_id"]
    check("track_id min == 10", int(ids.min()) == 10, str(int(ids.min())))
    check("track_id max == 124911", int(ids.max()) == 124911, str(int(ids.max())))
    check("track_id unique", ids.is_unique, f"{len(ids) - ids.nunique()} duplicates")

    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "--no-header", "tests/test_normalize.py"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    tail = (proc.stdout + proc.stderr).strip().splitlines()[-1:]
    check("normalization unit tests pass", proc.returncode == 0, " | ".join(tail))

    print()
    print("key distribution (top 5):")
    top = df["key_label_display"].value_counts().head(5)
    for label, count in top.items():
        print(f"  {label:<12} {count}")

    print()
    if FAILURES:
        print(f"GATE 1 FAILED: {len(FAILURES)} check(s) failed: {', '.join(FAILURES)}")
        return 1
    print("GATE 1 PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
