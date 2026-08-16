#!/usr/bin/env python
"""GATE 3 — the audio was really acquired, and the served clips are real audio.

  * build/acquisition.json exists, names the source used, records checksum results
  * >= 3000 annotated tracks obtained (below that the download silently truncated)
  * a random sample of 200 obtained ids resolves to NNN/NNNNNN.mp3 on disk, > 10 KB
  * mutagen opens 200 sampled *served clips* without exception, 25-35 s each
  * anything failing these is dropped with a logged reason, not silently skipped

The duration check applies to the derived clips because FMAK ships full-length
tracks, not the 30 s clips SPEC §1.3 assumed (see clips.py). The bound itself is
unchanged.
"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from tonic_trainer.clips import CLIP_REPORT, CLIP_ROOT, probe_duration  # noqa: E402
from tonic_trainer.phase3 import ACQUISITION_JSON, AUDIO_ROOT  # noqa: E402

MIN_OBTAINED = 3000
SAMPLE = 200
FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}{(' — ' + detail) if detail else ''}")
    if not ok:
        FAILURES.append(name)


def main() -> int:
    print("=== GATE 3 — audio acquisition ===")
    if not ACQUISITION_JSON.exists():
        print(f"GATE 3 FAILED: {ACQUISITION_JSON} missing")
        return 1

    acq = json.loads(ACQUISITION_JSON.read_text())
    check("acquisition.json names the source used", bool(acq.get("source")), str(acq.get("source")))

    checksums = acq.get("checksums") or []
    all_verified = bool(checksums) and all(c.get("verified") for c in checksums)
    check("every downloaded file verified against the record's checksum",
          all_verified, f"{sum(bool(c.get('verified')) for c in checksums)}/{len(checksums)} files")

    if acq.get("fallback_used"):
        check("fallback recorded its range probe", bool(acq.get("range_probe")), str(acq.get("range_probe")))

    if acq.get("sources_deleted"):
        # Not an excuse and not a pass: the checks below will fail, and this
        # line exists only so the reason is obvious rather than mysterious.
        print(f"NOTE: the source tracks were deleted on {acq['sources_deleted']} "
              f"({acq.get('sources_deleted_reason', 'no reason recorded')}).")
        print("      This gate verifies files on disk, so it now requires re-downloading")
        print("      the 39.3 GB package. Its original PASS is recorded in acquisition.json.")

    obtained = int(acq.get("annotated_tracks_obtained", 0))
    check(f"annotated tracks obtained >= {MIN_OBTAINED}", obtained >= MIN_OBTAINED, str(obtained))

    on_disk = {int(p.stem): p for p in AUDIO_ROOT.rglob("*.mp3")}
    check("source tracks on disk match the recorded count", len(on_disk) == obtained,
          f"{len(on_disk)} on disk vs {obtained} recorded")

    rng = random.Random(1729)
    sample_ids = rng.sample(sorted(on_disk), min(SAMPLE, len(on_disk)))
    # An empty sample would make every per-file check below pass vacuously.
    check(f"there are at least {SAMPLE} source tracks to sample",
          len(sample_ids) == SAMPLE, f"{len(sample_ids)} available")
    bad_path = []
    for tid in sample_ids:
        p = AUDIO_ROOT / f"{tid // 1000:03d}" / f"{tid:06d}.mp3"
        if not p.exists() or p.stat().st_size <= 10_240:
            bad_path.append(tid)
    check(f"{len(sample_ids)} sampled ids resolve to NNN/NNNNNN.mp3 > 10 KB",
          not bad_path, f"{len(bad_path)} bad: {bad_path[:5]}")

    clips = {int(p.stem): p for p in CLIP_ROOT.rglob("*.mp3")}
    check("derived clips exist", len(clips) > 0, f"{len(clips)} clips")

    clip_ids = rng.sample(sorted(clips), min(SAMPLE, len(clips)))
    check(f"there are at least {SAMPLE} clips to sample",
          len(clip_ids) == SAMPLE, f"{len(clip_ids)} available")
    bad_duration, unreadable = [], []
    for tid in clip_ids:
        try:
            d = probe_duration(clips[tid])
        except Exception as exc:  # noqa: BLE001 — the gate reports, then fails
            unreadable.append((tid, repr(exc)))
            continue
        if not (25.0 <= d <= 35.0):
            bad_duration.append((tid, round(d, 2)))
    check(f"mutagen opens {len(clip_ids)} sampled clips without exception",
          not unreadable, str(unreadable[:3]))
    check("sampled clip durations are 25-35 s", not bad_duration, str(bad_duration[:5]))

    if CLIP_REPORT.exists():
        rep = json.loads(CLIP_REPORT.read_text())
        check("clip drops are logged with reasons",
              rep["dropped"] == 0 or bool(rep["drop_reasons"]),
              f"dropped {rep['dropped']}: {rep['drop_reasons']}")
    else:
        check("clip report exists", False, str(CLIP_REPORT))

    print()
    print(f"source        : {acq.get('source')}")
    print(f"obtained      : {obtained} of {acq.get('annotated_tracks_expected')} annotated")
    print(f"missing       : {len(acq.get('missing_track_ids') or [])}")
    print(f"served clips  : {len(clips)}")

    print()
    if FAILURES:
        print(f"GATE 3 FAILED: {len(FAILURES)} check(s) failed: {', '.join(FAILURES)}")
        return 1
    print("GATE 3 PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
