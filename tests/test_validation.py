"""Phase 4b estimator tests.

Gate 4b judges the corpus by the *shape* of estimator disagreements, so the
estimator itself has to be known-good first — otherwise a failed gate is
ambiguous between "the join is broken" and "the analysis is broken". These
tests pin it against synthesized audio whose key is not in question.
"""

from __future__ import annotations

import subprocess

import numpy as np
import pytest

from tonic_trainer.scoring import EXACT, RELATIVE
from tonic_trainer.validation import (
    KK_MAJOR,
    KK_MINOR,
    chroma_vector,
    estimate_key,
    summarize,
)
from tonic_trainer.validation import TrackResult

TRIADS = {
    # name: (frequencies, expected (pitch class, mode))
    "c_major": ([261.63, 329.63, 392.00], (0, "major")),
    "a_minor": ([440.00, 523.25, 659.25], (9, "minor")),
    "f_major": ([349.23, 440.00, 523.25], (5, "major")),
    "d_minor": ([293.66, 349.23, 440.00], (2, "minor")),
}


@pytest.fixture(scope="session")
def triad_files(tmp_path_factory):
    root = tmp_path_factory.mktemp("triads")
    made = {}
    for name, (freqs, expected) in TRIADS.items():
        out = root / f"{name}.mp3"
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"]
        for f in freqs:
            cmd += ["-f", "lavfi", "-i", f"sine=frequency={f}:duration=8"]
        cmd += ["-filter_complex", f"amix=inputs={len(freqs)}", "-ac", "1", "-b:a", "128k", str(out)]
        subprocess.run(cmd, check=True)
        made[name] = (out, expected)
    return made


@pytest.mark.parametrize("name", sorted(TRIADS))
def test_estimator_recovers_a_known_triad(triad_files, name):
    path, expected = triad_files[name]
    assert estimate_key(chroma_vector(str(path))) == expected


def test_profiles_are_twelve_values_with_the_tonic_highest():
    for profile in (KK_MAJOR, KK_MINOR):
        assert len(profile) == 12
        assert profile.argmax() == 0  # indexed from the tonic


def test_estimator_rejects_a_flat_chroma():
    with pytest.raises(ValueError):
        estimate_key(np.ones(12))


def test_summary_separates_tonic_only_from_tonic_and_mode():
    results = [
        # exact
        TrackResult("a", 0, "major", 0, "major", EXACT, True, "Rock", "tier1"),
        # same tonic, wrong mode: counts for tonic-only, not for exact
        TrackResult("b", 0, "major", 0, "minor", "parallel", True, "Rock", "tier1"),
        # relative: neither tonic nor exact
        TrackResult("c", 0, "major", 9, "minor", RELATIVE, False, "Folk", "tier2"),
    ]
    s = summarize(results)
    assert s["n"] == 3
    assert s["exact_tonic_and_mode"] == 1
    assert s["tonic_only_matches"] == 2
    assert s["buckets"][RELATIVE] == 1
    assert s["exact_rate"] == pytest.approx(1 / 3)
