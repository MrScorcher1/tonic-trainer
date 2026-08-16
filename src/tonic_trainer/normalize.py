"""Normalize `fma_keys` key strings to (pitch class, mode) and back.

The source mixes spelling conventions: it writes pitch class 3 as ``D#`` and
pitch class 10 as ``Bb`` in the same file, and capitalizes ``Major`` while
lowercasing ``minor``. We keep three representations per row:

* ``tonic_pc`` + ``mode``  — the canonical machine form (C = 0).
* ``key_label_original``   — verbatim source string, for the round-trip check.
* ``key_label_display``    — conventional spelling, for the UI.

The round-trip table below encodes the *source's own* spelling per
(tonic_pc, mode) pair, not the conventional one — a conventional table cannot
round-trip 100% of rows because the source spells pc 3 as ``D#`` (SPEC Gate 1).
"""

from __future__ import annotations

from typing import Literal

import pandas as pd

Mode = Literal["major", "minor"]
MODES: tuple[Mode, Mode] = ("major", "minor")

# Every accidental spelling that could appear in a key string, mapped to a
# pitch class. Wider than the source's own vocabulary on purpose: an unexpected
# spelling should map correctly or raise, never be silently dropped.
PITCH_CLASS: dict[str, int] = {
    "C": 0, "B#": 0,
    "C#": 1, "Db": 1,
    "D": 2,
    "D#": 3, "Eb": 3,
    "E": 4, "Fb": 4,
    "F": 5, "E#": 5,
    "F#": 6, "Gb": 6,
    "G": 7,
    "G#": 8, "Ab": 8,
    "A": 9,
    "A#": 10, "Bb": 10,
    "B": 11, "Cb": 11,
}

# Source spelling per pitch class, transcribed from the 24 distinct
# `key_and_mode` values actually present in keys.csv. Both modes use the same
# 12 spellings there; they are listed per (pc, mode) anyway so a future source
# whose modes diverge would fail the round-trip loudly instead of quietly.
_SOURCE_NOTE_NAMES: dict[int, str] = {
    0: "C", 1: "C#", 2: "D", 3: "D#", 4: "E", 5: "F",
    6: "F#", 7: "G", 8: "G#", 9: "A", 10: "Bb", 11: "B",
}
_SOURCE_MODE_WORD: dict[Mode, str] = {"major": "Major", "minor": "minor"}

SOURCE_SPELLING: dict[tuple[int, Mode], str] = {
    (pc, mode): f"{name} {_SOURCE_MODE_WORD[mode]}"
    for pc, name in _SOURCE_NOTE_NAMES.items()
    for mode in MODES
}

# Conventional spelling for display: the key signature a musician would write.
# Differs from the source at pc 1 and 8 (major), and pc 3 (both modes).
_DISPLAY_NOTE_NAMES: dict[tuple[int, Mode], str] = {
    (0, "major"): "C", (1, "major"): "Db", (2, "major"): "D", (3, "major"): "Eb",
    (4, "major"): "E", (5, "major"): "F", (6, "major"): "F#", (7, "major"): "G",
    (8, "major"): "Ab", (9, "major"): "A", (10, "major"): "Bb", (11, "major"): "B",
    (0, "minor"): "C", (1, "minor"): "C#", (2, "minor"): "D", (3, "minor"): "Eb",
    (4, "minor"): "E", (5, "minor"): "F", (6, "minor"): "F#", (7, "minor"): "G",
    (8, "minor"): "G#", (9, "minor"): "A", (10, "minor"): "Bb", (11, "minor"): "B",
}

DISPLAY_SPELLING: dict[tuple[int, Mode], str] = {
    (pc, mode): f"{name} {_SOURCE_MODE_WORD[mode]}"
    for (pc, mode), name in _DISPLAY_NOTE_NAMES.items()
}


def parse_key_label(label: str) -> tuple[int, Mode]:
    """``"D# Major"`` -> ``(3, "major")``. Raises on anything unrecognized."""
    if not isinstance(label, str):
        raise TypeError(f"key label must be a string, got {type(label).__name__}: {label!r}")
    parts = label.strip().split()
    if len(parts) != 2:
        raise ValueError(f"unparseable key label: {label!r}")
    note, mode_word = parts
    if note not in PITCH_CLASS:
        raise ValueError(f"unknown note name {note!r} in key label {label!r}")
    mode_lower = mode_word.lower()
    if mode_lower not in MODES:
        raise ValueError(f"unknown mode {mode_word!r} in key label {label!r}")
    return PITCH_CLASS[note], mode_lower  # type: ignore[return-value]


def source_label(tonic_pc: int, mode: Mode) -> str:
    """Inverse of :func:`parse_key_label` in the source's own spelling."""
    key = (int(tonic_pc), mode)
    if key not in SOURCE_SPELLING:
        raise KeyError(f"no source spelling for {key}")
    return SOURCE_SPELLING[key]


def display_label(tonic_pc: int, mode: Mode) -> str:
    key = (int(tonic_pc), mode)
    if key not in DISPLAY_SPELLING:
        raise KeyError(f"no display spelling for {key}")
    return DISPLAY_SPELLING[key]


def normalize_keys(raw: pd.DataFrame) -> pd.DataFrame:
    """Raw keys.csv frame -> normalized frame. Raises on any unparseable row."""
    parsed = raw["key_and_mode"].map(parse_key_label)
    out = pd.DataFrame(
        {
            "track_id": raw["track_id"].astype(int),
            "tonic_pc": [pc for pc, _ in parsed],
            "mode": [m for _, m in parsed],
            "key_label_original": raw["key_and_mode"].astype(str),
        }
    )
    out["tonic_pc"] = out["tonic_pc"].astype(int)
    out["key_label_display"] = [
        display_label(pc, mode) for pc, mode in zip(out["tonic_pc"], out["mode"], strict=True)
    ]
    # Empty spotify_uri cells read back as NaN; keep them as a real null.
    uri = raw["spotify_uri"]
    out["spotify_uri"] = uri.where(uri.notna() & (uri.astype(str).str.strip() != ""), None)

    roundtrip = [
        source_label(pc, mode) for pc, mode in zip(out["tonic_pc"], out["mode"], strict=True)
    ]
    mismatches = (pd.Series(roundtrip, index=out.index) != out["key_label_original"]).sum()
    if mismatches:
        bad = out.loc[pd.Series(roundtrip, index=out.index) != out["key_label_original"]].head(5)
        raise ValueError(f"round-trip failed for {mismatches} rows; first offenders:\n{bad}")

    return out
