"""The `relative_error` taxonomy — shared by the server and by Phase 4b.

One definition, two callers: Phase 4b classifies estimator-vs-label
disagreements with exactly the buckets the server uses to classify
user-vs-answer misses, so the validation table and the UI speak the same
language (SPEC Phase 4b, Phase 5).
"""

from __future__ import annotations

from typing import Literal

Mode = Literal["major", "minor"]

EXACT = "exact"
RELATIVE = "relative"
PARALLEL = "parallel"
SEMITONE = "semitone"
FIFTH = "fifth"
OTHER = "other"

BUCKETS = (EXACT, RELATIVE, PARALLEL, SEMITONE, FIFTH, OTHER)
NON_EXACT = (RELATIVE, PARALLEL, SEMITONE, FIFTH, OTHER)


def classify(guess_pc: int, guess_mode: str, actual_pc: int, actual_mode: str) -> str:
    """Classify a guess against the answer.

    * ``exact``    — tonic and mode both right.
    * ``relative`` — same pitch collection, wrong mode (C major vs A minor).
      The relative minor sits 3 semitones below its major. This is the
      pedagogically interesting miss.
    * ``parallel`` — right tonic, wrong mode (C major vs C minor).
    * ``semitone`` — tonic off by one semitone either way.
    * ``fifth``    — tonic off by a fifth either way (7 or 5 semitones).
    * ``other``    — everything else.
    """
    guess_pc = int(guess_pc) % 12
    actual_pc = int(actual_pc) % 12
    if guess_mode not in ("major", "minor") or actual_mode not in ("major", "minor"):
        raise ValueError(f"mode must be 'major' or 'minor', got {guess_mode!r} / {actual_mode!r}")

    if guess_pc == actual_pc and guess_mode == actual_mode:
        return EXACT

    if guess_mode != actual_mode:
        if guess_pc == actual_pc:
            return PARALLEL
        # relative major of a minor key is 3 semitones up; relative minor of a
        # major key is 3 semitones down.
        if guess_mode == "minor" and guess_pc == (actual_pc - 3) % 12:
            return RELATIVE
        if guess_mode == "major" and guess_pc == (actual_pc + 3) % 12:
            return RELATIVE

    interval = (guess_pc - actual_pc) % 12
    if interval in (1, 11):
        return SEMITONE
    if interval in (5, 7):
        return FIFTH
    return OTHER


def explain(bucket: str, key_display: str, guess_display: str) -> str:
    """Plain-language reading of a miss, for the result panel."""
    if bucket == EXACT:
        return f"Exactly right — the key is {key_display}."
    if bucket == RELATIVE:
        return (
            f"You found the right notes but heard the wrong home. You guessed {guess_display}; "
            f"the answer is {key_display} — its relative key. Same seven notes, different tonic."
        )
    if bucket == PARALLEL:
        return (
            f"Right tonic, wrong colour. You guessed {guess_display}; the answer is {key_display}. "
            "Listen to the third above the tonic."
        )
    if bucket == SEMITONE:
        return (
            f"One semitone off. You guessed {guess_display}; the answer is {key_display}. "
            "Your drone was a hair sharp or flat against the track."
        )
    if bucket == FIFTH:
        return (
            f"Off by a fifth. You guessed {guess_display}; the answer is {key_display}. "
            "Easy to lock onto the dominant instead of the tonic."
        )
    return f"Not this time. You guessed {guess_display}; the answer is {key_display}."
