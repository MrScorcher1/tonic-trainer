"""Phase 4b — statistical validation that the labels describe the audio.

Every earlier gate can pass while the manifest is systematically wrong: right
audio, right labels, joined on the wrong key. This is the only check that would
notice.

Krumhansl-Schmuckler key estimation over a CQT chroma, compared against the
manifest label with the same taxonomy the server uses. The diagnostic power is
in the *shape* of the disagreements, not the raw accuracy — a correct pairing
shows a solid exact plurality with `relative` as the largest error bucket. This
never adjudicates an individual label (SPEC Phase 4b).
"""

from __future__ import annotations

import json
import random
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, dataclass

import librosa
import numpy as np

from .clips import CLIP_ROOT
from .paths import BUILD
from .scoring import BUCKETS, EXACT, NON_EXACT, classify

VALIDATION_JSON = BUILD / "validation.json"

# Krumhansl-Kessler probe-tone profiles (Krumhansl, "Cognitive Foundations of
# Musical Pitch", 1990), indexed from the tonic.
KK_MAJOR = np.array(
    [6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88]
)
KK_MINOR = np.array(
    [6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17]
)

SAMPLE_SIZE = 300
CONTROL_SIZE = 100


@dataclass
class TrackResult:
    id: str
    label_pc: int
    label_mode: str
    predicted_pc: int
    predicted_mode: str
    bucket: str
    tonic_match: bool
    genre_top: str | None
    difficulty: str


def chroma_vector(path: str) -> np.ndarray:
    """Time-averaged CQT chroma, C at index 0."""
    y, sr = librosa.load(path, sr=22050, mono=True)
    if y.size == 0:
        raise ValueError(f"{path}: decoded to zero samples")
    chroma = librosa.feature.chroma_cqt(y=y, sr=sr)
    vector = chroma.mean(axis=1)
    if not np.isfinite(vector).all():
        raise ValueError(f"{path}: chroma contains non-finite values")
    return vector


def estimate_key(vector: np.ndarray) -> tuple[int, str]:
    """Krumhansl-Schmuckler: correlate the chroma against all 24 rotated profiles."""
    v = vector - vector.mean()
    denom_v = np.linalg.norm(v)
    if denom_v == 0:
        raise ValueError("chroma vector is flat — no tonal information")

    best = (-2.0, 0, "major")
    for mode, profile in (("major", KK_MAJOR), ("minor", KK_MINOR)):
        for pc in range(12):
            rotated = np.roll(profile, pc)
            p = rotated - rotated.mean()
            corr = float(np.dot(v, p) / (denom_v * np.linalg.norm(p)))
            if corr > best[0]:
                best = (corr, pc, mode)
    return best[1], best[2]


def _analyse(entry: dict) -> tuple[str, int, str] | tuple[str, None, str]:
    """Worker: returns (id, predicted_pc, predicted_mode) or (id, None, error)."""
    try:
        vector = chroma_vector(str(CLIP_ROOT / entry["audio_path"]))
        pc, mode = estimate_key(vector)
        return entry["id"], pc, mode
    except Exception as exc:  # noqa: BLE001 — reported per track, never swallowed
        return entry["id"], None, f"{type(exc).__name__}: {exc}"


def analyse_entries(entries: list[dict], workers: int = 12) -> dict[str, tuple[int, str]]:
    predictions: dict[str, tuple[int, str]] = {}
    errors: list[tuple[str, str]] = []
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for i, (pid, pc, mode) in enumerate(pool.map(_analyse, entries, chunksize=4), start=1):
            if pc is None:
                errors.append((pid, mode))
            else:
                predictions[pid] = (pc, mode)
            if i % 50 == 0:
                print(f"  analysed {i}/{len(entries)}", flush=True)
    if errors:
        raise RuntimeError(
            f"{len(errors)} clips failed analysis — the manifest should not contain "
            f"unanalysable audio. First: {errors[:3]}"
        )
    return predictions


def bucket_counts(results: list[TrackResult]) -> dict[str, int]:
    counts = {b: 0 for b in BUCKETS}
    for r in results:
        counts[r.bucket] += 1
    return counts


def summarize(results: list[TrackResult]) -> dict:
    n = len(results)
    counts = bucket_counts(results)
    tonic_only = sum(r.tonic_match for r in results)
    return {
        "n": n,
        "exact_tonic_and_mode": counts[EXACT],
        "exact_rate": counts[EXACT] / n if n else 0.0,
        "tonic_only_matches": tonic_only,
        "tonic_only_rate": tonic_only / n if n else 0.0,
        "buckets": counts,
        "bucket_rates": {k: (v / n if n else 0.0) for k, v in counts.items()},
    }


def print_summary(title: str, summary: dict) -> None:
    n = summary["n"]
    print()
    print(f"--- {title} (n={n}) ---")
    print(f"{'bucket':<12} {'count':>6} {'share':>8}")
    for bucket in BUCKETS:
        count = summary["buckets"][bucket]
        print(f"{bucket:<12} {count:>6} {count / n if n else 0:>7.1%}")
    print(f"{'-' * 28}")
    print(f"{'tonic only':<12} {summary['tonic_only_matches']:>6} {summary['tonic_only_rate']:>7.1%}")
    print(f"{'tonic+mode':<12} {summary['exact_tonic_and_mode']:>6} {summary['exact_rate']:>7.1%}")


def run(sample_size: int | None = None, control_size: int = CONTROL_SIZE,
        seed: int = 20260816, workers: int = 12) -> dict:
    """Analyse the ENTIRE tier1+tier2 pool — a census, not a sample.

    The original method drew 300 random entries. That made the verdict depend on
    a seed, which is exactly the knob that could be turned until the gate went
    green; a census has no such knob. See gates/gate4b.py for the full record of
    why the method changed and when.

    `sample_size` is retained only so a caller can deliberately run a smaller,
    noisier check; the gate itself requires the census.
    """
    from .manifest import load_manifest

    entries = load_manifest()
    pool = [e for e in entries if e["difficulty"] in ("tier1", "tier2")]
    if not pool:
        raise ValueError("no tier1+tier2 entries to validate")

    rng = random.Random(seed)
    if sample_size is None:
        sample = list(pool)
        print(f"analysing ALL {len(sample)} tier1+tier2 clips (census) "
              f"with Krumhansl-Schmuckler ...")
    else:
        if len(pool) < sample_size:
            raise ValueError(f"only {len(pool)} tier1+tier2 entries, need {sample_size}")
        sample = rng.sample(pool, sample_size)
        print(f"analysing {len(sample)} sampled tier1+tier2 clips "
              f"with Krumhansl-Schmuckler ...")
    predictions = analyse_entries(sample, workers=workers)

    results = [
        TrackResult(
            id=e["id"],
            label_pc=e["tonic_pc"],
            label_mode=e["mode"],
            predicted_pc=predictions[e["id"]][0],
            predicted_mode=predictions[e["id"]][1],
            bucket=classify(predictions[e["id"]][0], predictions[e["id"]][1], e["tonic_pc"], e["mode"]),
            tonic_match=predictions[e["id"]][0] == e["tonic_pc"],
            genre_top=e["genre_top"],
            difficulty=e["difficulty"],
        )
        for e in sample
    ]
    real = summarize(results)

    # Negative control: same predictions, labels shuffled against them. If a
    # broken pairing scores near the real one, the metric itself is meaningless.
    # Run over the whole pool too, so the control is not a sampled estimate either.
    control_entries = list(sample) if sample_size is None else rng.sample(
        sample, min(control_size, len(sample)))
    shuffled_labels = [(e["tonic_pc"], e["mode"]) for e in control_entries]
    rng.shuffle(shuffled_labels)
    control_results = []
    for e, (pc, mode) in zip(control_entries, shuffled_labels, strict=True):
        pred_pc, pred_mode = predictions[e["id"]]
        control_results.append(
            TrackResult(
                id=e["id"], label_pc=pc, label_mode=mode,
                predicted_pc=pred_pc, predicted_mode=pred_mode,
                bucket=classify(pred_pc, pred_mode, pc, mode),
                tonic_match=pred_pc == pc,
                genre_top=e["genre_top"], difficulty=e["difficulty"],
            )
        )
    control = summarize(control_results)

    by_genre: dict[str, dict] = {}
    for genre in sorted({r.genre_top or "(untagged)" for r in results}):
        subset = [r for r in results if (r.genre_top or "(untagged)") == genre]
        by_genre[genre] = summarize(subset)

    payload = {
        "seed": seed,
        "census": sample_size is None,
        "pool_size": len(pool),
        "sample": real,
        "negative_control": control,
        "by_genre": by_genre,
        "largest_non_exact_bucket": max(NON_EXACT, key=lambda b: real["buckets"][b]),
        "tracks": [asdict(r) for r in results],
    }
    VALIDATION_JSON.write_text(json.dumps(payload, indent=1))

    print_summary("real pairing, tier1+tier2", real)
    print_summary("negative control (labels shuffled)", control)
    print()
    print("by genre (exact = tonic+mode):")
    print(f"{'genre':<22} {'n':>5} {'tonic':>8} {'exact':>8}")
    for genre, summary in sorted(by_genre.items(), key=lambda kv: -kv[1]["n"]):
        print(f"{genre:<22} {summary['n']:>5} {summary['tonic_only_rate']:>7.1%} {summary['exact_rate']:>7.1%}")
    print()
    print(f"written: {VALIDATION_JSON}")
    return payload


if __name__ == "__main__":
    run()
