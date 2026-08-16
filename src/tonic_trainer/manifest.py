"""Phase 4 — build the puzzle manifest.

One entry per servable puzzle: the derived 30-second clip, the answer, and the
attribution that must be displayed with it. A track that cannot be attributed
never becomes a puzzle (SPEC §1.4).

Difficulty is a static genre prior only. The spec's original response-history
tiering needed per-response storage, which the stateless architecture forbids
(SPEC §0.5), so the prior is the whole tier system.
"""

from __future__ import annotations

import json
import re
from typing import Iterable

import pandas as pd

from .clips import CLIP_ROOT
from .paths import BUILD

MANIFEST_JSON = BUILD / "manifest.json"

# Gate 5 asserts that a served puzzle's raw JSON contains none of the strings
# `tonic_pc`, `mode`, `key_display` — a pure string check, deliberately blind to
# structure, because that is what catches an answer leaking through an
# unexpected field. Attribution text can contain those substrings innocently
# ("Modern Man", "Airplane Mode"): 12 of 3636 usable tracks do. Those 12 are
# dropped rather than making the leak check fuzzy. Losing 0.3% of the corpus is
# cheaper than an answer-leak test that has to reason about which "mode" is real.
# The second alternative is Gate 6's rule: the page must contain no text
# matching a key name before submission. Exactly one usable track is called
# "Prelude In F Major" — displaying its title would trip the answer-leak
# detector for reasons that have nothing to do with a leak.
LEAK_WORDS = re.compile(r"tonic_pc|mode|key_display", re.IGNORECASE)

# Case-insensitive, with a word boundary before the note letter. The earlier
# case-sensitive form missed a real leak: "Prelude In D Minor" IS in D minor, and
# the title is on screen before the user answers — capital "Minor" walked straight
# through. The boundary is what keeps the rule from eating innocent prose: it
# drops "In D Minor" but keeps "The Minor Thirds" and "Sea Minor", where the
# apparent note letter is the tail of another word.
KEY_NAME_IN_TEXT = re.compile(r"\b[A-G][#b]?\s+(major|minor)\b", re.IGNORECASE)

TIER1_GENRES = frozenset({"Rock", "Folk", "Pop", "Blues", "Country"})

# `untagged` was originally excluded from the default pool on the spec's claim
# that those tracks "skew experimental/ambient and may have no audible tonal
# center". That premise was measured over every clip in each pool and FAILED:
#
#     untagged      n=1929   exact 51.5%   tonic-only 58.7%
#     tier3         n= 766   exact 49.9%   tonic-only 60.4%
#     tier1+tier2   n= 627   exact 45.9%   tonic-only 57.3%
#     (chance under an independent pairing: ~4.2% / ~8.3%)
#
# Untagged is 12x chance and scores HIGHER than the pool that was being served.
# The same measurement showed the tier prior inverts: tier3 outscores
# tier1+tier2, so genre does not rank tonal clarity here. The default changed
# because the stated premise was tested and did not hold — not because a bigger
# corpus was preferred. Reproduce with tools/untagged_check.py.
#
# OPEN QUESTION THE NUMBERS CANNOT SETTLE: Krumhansl-Schmuckler measures tonal
# clarity as the ALGORITHM sees it. Sustained ambient material yields a clean,
# stable chroma and can score well while making a poor drone-hunting exercise —
# a track that is itself a drone defeats the exercise. A listening test on the
# untagged pool has NOT been performed. A future reader should assume it is
# still open rather than that it was done and passed.
DEFAULT_POOL = ("tier1", "tier2", "tier3", "untagged")
TAGGED_POOL = ("tier1", "tier2", "tier3")  # `tier=tagged` turns untagged back off
MAX_KEY_SHARE = 0.25


def assign_tier(genre_top: object, mode: str) -> str:
    if genre_top is None or (isinstance(genre_top, float) and pd.isna(genre_top)):
        return "untagged"
    genre = str(genre_top).strip()
    if not genre:
        return "untagged"
    if genre in TIER1_GENRES:
        return "tier1" if mode == "major" else "tier2"
    return "tier3"


def puzzle_id(track_id: int) -> str:
    return f"fma-{int(track_id):06d}"


def build_manifest(joined: pd.DataFrame, clip_paths: dict[int, str]) -> list[dict]:
    """Rows that are usable *and* have a derived clip become puzzles."""
    entries: list[dict] = []
    leak_dropped: list[int] = []
    for row in joined.itertuples(index=False):
        if not row.usable:
            continue
        rel = clip_paths.get(int(row.track_id))
        if rel is None:
            continue
        genre_text = "" if pd.isna(row.genre_top) else str(row.genre_top)
        attribution_text = f"{row.title} {row.artist} {genre_text}"
        if LEAK_WORDS.search(attribution_text) or KEY_NAME_IN_TEXT.search(attribution_text):
            leak_dropped.append(int(row.track_id))
            continue
        title = str(row.title).strip()
        artist = str(row.artist).strip()
        license_str = str(row.license).strip()
        if not title or not artist or not license_str:
            raise ValueError(f"track {row.track_id} reached the manifest without attribution")
        genre = None if pd.isna(row.genre_top) else str(row.genre_top)
        entries.append(
            {
                "id": puzzle_id(row.track_id),
                "audio_path": rel,
                "tonic_pc": int(row.tonic_pc),
                "mode": str(row.mode),
                "key_display": str(row.key_label_display),
                "title": title,
                "artist": artist,
                "license": license_str,
                "license_canonical": str(row.license_canonical),
                "genre_top": genre,
                "difficulty": assign_tier(row.genre_top, str(row.mode)),
            }
        )
    if leak_dropped:
        print(f"dropped {len(leak_dropped)} tracks whose attribution contains a leak-check "
              f"substring (see LEAK_WORDS): {leak_dropped[:6]}")
    if not entries:
        raise ValueError("manifest is empty — no usable track had a derived clip")
    return entries


def served_pool(entries: Iterable[dict], tiers: Iterable[str] = DEFAULT_POOL) -> list[dict]:
    wanted = set(tiers)
    return [e for e in entries if e["difficulty"] in wanted]


def key_distribution(entries: Iterable[dict]) -> pd.Series:
    labels = [e["key_display"] for e in entries]
    return pd.Series(labels).value_counts()


def write_manifest(entries: list[dict]) -> None:
    MANIFEST_JSON.write_text(json.dumps(entries, indent=1))


def load_manifest() -> list[dict]:
    if not MANIFEST_JSON.exists():
        raise FileNotFoundError(f"{MANIFEST_JSON} does not exist — run phase 4 first")
    return json.loads(MANIFEST_JSON.read_text())


def build() -> list[dict]:
    from .phase2 import JOINED_PARQUET

    joined = pd.read_parquet(JOINED_PARQUET)
    # Tracks the licence cross-check found ND evidence for stay out on a rebuild
    # too — otherwise re-running this phase would quietly re-admit them.
    nd_excluded_path = BUILD / "nd_excluded.json"
    nd_excluded = set(json.loads(nd_excluded_path.read_text())) if nd_excluded_path.exists() else set()
    if nd_excluded:
        print(f"licence cross-check excludes {len(nd_excluded)} ND-flagged tracks")
    clip_paths = {}
    for path in CLIP_ROOT.rglob("*.mp3"):
        if path.stat().st_size > 0:
            clip_paths[int(path.stem)] = str(path.relative_to(CLIP_ROOT))
    print(f"clips on disk: {len(clip_paths)}")

    entries = [e for e in build_manifest(joined, clip_paths) if e["id"] not in nd_excluded]
    write_manifest(entries)

    tiers = pd.Series([e["difficulty"] for e in entries]).value_counts()
    print(f"manifest entries: {len(entries)}")
    for tier, n in tiers.items():
        print(f"  {tier:<10} {n}")

    pool = served_pool(entries)
    dist = key_distribution(pool)
    print(f"\nserved pool (excludes untagged): {len(pool)}")
    print("key distribution (top 10):")
    for label, n in dist.head(10).items():
        print(f"  {label:<12} {n:>5}  {n / len(pool):.1%}")
    top_share = dist.iloc[0] / len(pool)
    if top_share > MAX_KEY_SHARE:
        print(f"WARNING: {dist.index[0]} is {top_share:.1%} of the pool, over the {MAX_KEY_SHARE:.0%} cap")
    print(f"\nwritten: {MANIFEST_JSON}")
    return entries


if __name__ == "__main__":
    build()
