"""Why is `relative` not the largest error bucket? — evidence, not opinion.

Gate 4b expects a correct pairing to show `relative` as the biggest non-exact
bucket. The real run shows `fifth` instead, with a healthy exact rate and a
collapsed negative control. Two candidate explanations are testable, so this
tests them on the same 300 tracks rather than arguing about them:

  A. Clip position — openings may be less tonally settled than track middles.
  B. Chroma front-end — a raw CQT chroma carries percussive and transient
     energy; harmonic separation and CENS are the usual fixes.

Run:  .venv/bin/python tools/estimator_experiment.py
"""

from __future__ import annotations

import json
import random
import subprocess
import sys
import tempfile
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import librosa
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tonic_trainer.clips import CLIP_ROOT  # noqa: E402
from tonic_trainer.manifest import load_manifest  # noqa: E402
from tonic_trainer.phase3 import AUDIO_ROOT  # noqa: E402
from tonic_trainer.scoring import BUCKETS, classify  # noqa: E402
from tonic_trainer.validation import estimate_key  # noqa: E402

SEED = 20260816
SAMPLE = 300
MIDDLE_DIR = Path(tempfile.gettempdir()) / "tt-middle-clips"


def chroma(path: str, kind: str) -> np.ndarray:
    y, sr = librosa.load(path, sr=22050, mono=True)
    if kind == "cqt":
        c = librosa.feature.chroma_cqt(y=y, sr=sr)
    elif kind == "harmonic":
        c = librosa.feature.chroma_cqt(y=librosa.effects.harmonic(y), sr=sr)
    elif kind == "cens":
        c = librosa.feature.chroma_cens(y=y, sr=sr)
    else:
        raise ValueError(f"unknown chroma kind {kind!r}")
    return c.mean(axis=1)


def cut_middle(args: tuple[int, str]) -> tuple[int, str | None]:
    track_id, rel = args
    src = AUDIO_ROOT / rel
    out = MIDDLE_DIR / rel
    if out.exists():
        return track_id, str(out)
    from mutagen.mp3 import MP3

    duration = float(MP3(str(src)).info.length)
    start = max(0.0, (duration - 30.0) / 2.0)
    out.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
         "-ss", f"{start:.3f}", "-t", "30", "-i", str(src), "-vn", "-ac", "1",
         "-ar", "44100", "-b:a", "128k", "-map_metadata", "-1", str(out)],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        return track_id, None
    return track_id, str(out)


def _job(args: tuple[str, str, int, str]) -> tuple[str, int, str]:
    path, kind, label_pc, label_mode = args
    pc, mode = estimate_key(chroma(path, kind))
    return classify(pc, mode, label_pc, label_mode), int(pc == label_pc), mode


def evaluate(name: str, jobs: list[tuple[str, str, int, str]], workers: int = 12) -> dict:
    with ProcessPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(_job, jobs, chunksize=4))
    buckets = Counter(r[0] for r in results)
    n = len(results)
    tonic = sum(r[1] for r in results)
    summary = {
        "variant": name, "n": n,
        "exact": buckets["exact"] / n, "tonic_only": tonic / n,
        "buckets": {b: buckets[b] / n for b in BUCKETS},
        "largest_non_exact": max((b for b in BUCKETS if b != "exact"), key=lambda b: buckets[b]),
    }
    print(f"\n--- {name} (n={n}) ---")
    for b in BUCKETS:
        print(f"  {b:<10} {buckets[b]:>4}  {buckets[b] / n:>6.1%}")
    print(f"  tonic-only {tonic:>4}  {tonic / n:>6.1%}   largest non-exact: {summary['largest_non_exact']}")
    return summary


def main() -> None:
    entries = load_manifest()
    pool = [e for e in entries if e["difficulty"] in ("tier1", "tier2")]
    sample = random.Random(SEED).sample(pool, SAMPLE)  # same 300 as the real run

    summaries = []
    for kind in ("cqt", "harmonic", "cens"):
        jobs = [(str(CLIP_ROOT / e["audio_path"]), kind, e["tonic_pc"], e["mode"]) for e in sample]
        summaries.append(evaluate(f"openings / {kind}", jobs))

    print("\ncutting middle clips for the same tracks ...")
    MIDDLE_DIR.mkdir(parents=True, exist_ok=True)
    with ProcessPoolExecutor(max_workers=12) as pool_exec:
        cut = dict(pool_exec.map(
            cut_middle,
            [(int(e["id"].split("-")[1]), e["audio_path"]) for e in sample],
            chunksize=4,
        ))
    failed = [k for k, v in cut.items() if v is None]
    if failed:
        raise RuntimeError(f"{len(failed)} middle cuts failed: {failed[:5]}")

    for kind in ("cqt", "harmonic"):
        jobs = [(cut[int(e["id"].split("-")[1])], kind, e["tonic_pc"], e["mode"]) for e in sample]
        summaries.append(evaluate(f"middles / {kind}", jobs))

    out = Path(__file__).resolve().parents[1] / "build" / "estimator_experiment.json"
    out.write_text(json.dumps(summaries, indent=1))
    print(f"\nwritten: {out}")
    print("\nsummary:")
    print(f"{'variant':<22} {'exact':>7} {'tonic':>7} {'relative':>9} {'fifth':>7}  largest non-exact")
    for s in summaries:
        print(f"{s['variant']:<22} {s['exact']:>6.1%} {s['tonic_only']:>6.1%} "
              f"{s['buckets']['relative']:>8.1%} {s['buckets']['fifth']:>6.1%}  {s['largest_non_exact']}")


if __name__ == "__main__":
    main()
