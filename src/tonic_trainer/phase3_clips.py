"""Phase 3b — derive the served 30-second clips from the acquired full tracks.

Only license-allowed tracks get a clip. Cutting a loop out of a NoDerivatives
track would be exactly the derivative work §1.4 forbids, so those files are
never processed, not merely filtered later.
"""

from __future__ import annotations

import json

import pandas as pd

from .clips import build_clips, write_report
from .phase2 import JOINED_PARQUET
from .phase3 import ACQUISITION_JSON, AUDIO_ROOT


def build(workers: int = 12) -> dict:
    if not ACQUISITION_JSON.exists():
        raise FileNotFoundError(f"{ACQUISITION_JSON} missing — run phase3 first")
    acquisition = json.loads(ACQUISITION_JSON.read_text())
    obtained: dict[int, str] = {}
    for path in AUDIO_ROOT.rglob("*.mp3"):
        obtained[int(path.stem)] = str(path.relative_to(AUDIO_ROOT))
    print(f"source tracks on disk: {len(obtained)} "
          f"(acquisition.json recorded {acquisition['annotated_tracks_obtained']})")

    joined = pd.read_parquet(JOINED_PARQUET)
    usable = joined[joined["usable"]]
    jobs = [
        (int(tid), str(AUDIO_ROOT / obtained[int(tid)]), obtained[int(tid)])
        for tid in usable["track_id"]
        if int(tid) in obtained
    ]
    print(f"usable tracks with audio: {len(jobs)} — deriving 30 s middle clips")

    results = build_clips(jobs, workers=workers)
    report = write_report(results)
    print(f"clips ok: {report['ok']}, dropped: {report['dropped']}")
    for reason, n in report["drop_reasons"].items():
        print(f"  {n:>5}  {reason}")
    return report


if __name__ == "__main__":
    build()
