"""Computed per-song difficulty (1-3), replacing the tier scheme.

The tier labels claimed a ranking that measurement refuted — tier1 scored
*worst* on estimator agreement (45.9%) against tier3's 49.9% and untagged's
51.5%. The prior did not merely fail to predict difficulty, it inverted. So
difficulty is computed per song here, and genre goes back to being what it
always was: a filter.

Two margins, because the player makes two decisions
---------------------------------------------------
`tonic_margin` = correlation at the labeled key minus the best correlation at
any *different tonic*. Large means one pitch clearly wins and is easy to hunt;
small or negative means competing candidates (usually the fifth).

`mode_margin` = correlation at the labeled key minus the better of its relative
and parallel keys. Large means the third is clearly present; small means the
classic relative/parallel confusion is live.

Both are measured against the **labeled** key, not the estimator's prediction:
the player's task is finding the *correct* answer, so the question is how
clearly the audio points at the right key. A negative margin is a legitimately
very hard clip, not an error.

The degenerate-material guard
-----------------------------
A sustained drone earns a huge `tonic_margin` and would be rated easy — while
being a terrible puzzle, because droning a reference pitch against a track that
is itself a drone is a meaningless exercise. Margins are blind to this, so
`chroma_variance` (temporal variance of the chroma) overrides both extremes:
too static means trivial-or-unrewarding, too volatile means the tonal centre
moves. Both are forced to difficulty 3 and logged, never silently dropped.

Weights are NOT tuned
---------------------
The two margins are summed with equal weight because they are the same unit (a
correlation difference) and there is no data to justify anything else. Tuning
the weights until the distribution "looks right" would be the estimator-tuning
sin in a new costume; the player-rating loop is what is supposed to correct
them.
"""

from __future__ import annotations

import json
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, dataclass

import librosa
import numpy as np

from .clips import CLIP_ROOT
from .paths import BUILD
from .validation import key_correlations

DIFFICULTY_JSON = BUILD / "difficulty.json"

# Equal weights, deliberately unturned — see the module docstring.
TONIC_WEIGHT = 1.0
MODE_WEIGHT = 1.0

# Percentile cutoffs for the degenerate-material guard. Both extremes are
# hard-or-bad; the middle is where difficulty tracks the margins.
LOW_VARIANCE_PCTILE = 2.0
HIGH_VARIANCE_PCTILE = 98.0

LEVELS = (1, 2, 3)
MIN_PER_LEVEL = 200


@dataclass
class ClipMetrics:
    id: str
    tonic_margin: float
    mode_margin: float
    chroma_variance: float
    score: float


def analyse_clip(args: tuple[str, str, int, str]) -> ClipMetrics | tuple[str, str]:
    """Margins and chroma variance for one clip, or (id, error)."""
    puzzle_id, rel_path, tonic_pc, mode = args
    try:
        y, sr = librosa.load(str(CLIP_ROOT / rel_path), sr=22050, mono=True)
        chroma = librosa.feature.chroma_cqt(y=y, sr=sr)
        vector = chroma.mean(axis=1)
        corr = key_correlations(vector)

        labeled = corr[(tonic_pc, mode)]
        other_tonic = max(v for (pc, _m), v in corr.items() if pc != tonic_pc)
        relative = corr[((tonic_pc - 3) % 12, "minor")] if mode == "major" \
            else corr[((tonic_pc + 3) % 12, "major")]
        parallel = corr[(tonic_pc, "minor" if mode == "major" else "major")]

        tonic_margin = labeled - other_tonic
        mode_margin = labeled - max(relative, parallel)
        # Mean per-bin temporal variance: how much the chroma moves over the clip.
        variance = float(np.mean(np.var(chroma, axis=1)))
        score = TONIC_WEIGHT * tonic_margin + MODE_WEIGHT * mode_margin
        return ClipMetrics(puzzle_id, float(tonic_margin), float(mode_margin), variance, float(score))
    except Exception as exc:  # noqa: BLE001 — reported per clip, never swallowed
        return puzzle_id, f"{type(exc).__name__}: {exc}"


def analyse(entries: list[dict], workers: int = 12) -> list[ClipMetrics]:
    jobs = [(e["id"], e["audio_path"], e["tonic_pc"], e["mode"]) for e in entries]
    metrics: list[ClipMetrics] = []
    errors: list[tuple[str, str]] = []
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for i, result in enumerate(pool.map(analyse_clip, jobs, chunksize=4), start=1):
            if isinstance(result, ClipMetrics):
                metrics.append(result)
            else:
                errors.append(result)
            if i % 500 == 0:
                print(f"  analysed {i}/{len(jobs)}", flush=True)
    if errors:
        raise RuntimeError(
            f"{len(errors)} clips failed difficulty analysis — the manifest should not "
            f"contain unanalysable audio. First: {errors[:3]}"
        )
    return metrics


def bin_difficulty(metrics: list[ClipMetrics]) -> tuple[dict[str, int], dict]:
    """Terciles of the corpus, then the variance guard. Deterministic.

    Terciles are deliberate: they guarantee a usable spread (an absolute
    threshold could pile 90% of the corpus into one level) and they bound the
    claim — difficulty 3 means "harder than most songs in this corpus", not an
    absolute statement about music.
    """
    scores = np.array([m.score for m in metrics], dtype=float)
    variances = np.array([m.chroma_variance for m in metrics], dtype=float)

    easy_cut, hard_cut = np.quantile(scores, [2 / 3, 1 / 3])
    low_var = float(np.percentile(variances, LOW_VARIANCE_PCTILE))
    high_var = float(np.percentile(variances, HIGH_VARIANCE_PCTILE))

    ratings: dict[str, int] = {}
    forced: list[dict] = []
    for m in metrics:
        if m.score >= easy_cut:
            level = 1
        elif m.score >= hard_cut:
            level = 2
        else:
            level = 3
        if m.chroma_variance <= low_var or m.chroma_variance >= high_var:
            reason = ("static/drone material — its margin is lying about the experience"
                      if m.chroma_variance <= low_var
                      else "the tonal centre moves across the clip")
            if level != 3:
                forced.append({"id": m.id, "from": level, "chroma_variance": m.chroma_variance,
                               "reason": reason})
            level = 3
        ratings[m.id] = level

    cut_points = {
        "easy_cut_score": float(easy_cut),
        "hard_cut_score": float(hard_cut),
        "low_variance": low_var,
        "high_variance": high_var,
        "tonic_weight": TONIC_WEIGHT,
        "mode_weight": MODE_WEIGHT,
        "forced_to_3": forced,
    }
    return ratings, cut_points


def build(entries: list[dict] | None = None, workers: int = 12) -> dict:
    from .manifest import base_entries

    # base_entries(), not load_manifest(): difficulty is computed BEFORE the
    # manifest exists, and both must see exactly the same clip set.
    entries = entries if entries is not None else base_entries()
    print(f"computing difficulty for {len(entries)} clips ...")
    metrics = analyse(entries, workers=workers)
    ratings, cut_points = bin_difficulty(metrics)

    payload = {
        "cut_points": cut_points,
        "metrics": {m.id: asdict(m) for m in metrics},
        "ratings": ratings,
    }
    DIFFICULTY_JSON.write_text(json.dumps(payload, indent=1))

    counts = {level: sum(1 for v in ratings.values() if v == level) for level in LEVELS}
    print()
    print(f"tercile cut points: easy >= {cut_points['easy_cut_score']:.4f}, "
          f"hard < {cut_points['hard_cut_score']:.4f}")
    print(f"variance guard: <= {cut_points['low_variance']:.5f} or "
          f">= {cut_points['high_variance']:.5f} -> forced to 3 "
          f"({len(cut_points['forced_to_3'])} clips moved)")
    for level in LEVELS:
        print(f"  difficulty {level}: {counts[level]}")
    print(f"written: {DIFFICULTY_JSON}")
    return payload


if __name__ == "__main__":
    build()
