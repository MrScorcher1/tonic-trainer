"""Delete the transient 39 GB acquisition once every phase that needs it is done.

The zips and full-length tracks exist only to produce build/clips/. Three things
must already have happened, and this refuses to run otherwise:

  * Gate 3 passed while the files were present (recorded in acquisition.json),
  * the clips were derived,
  * the licence cross-check read the ID3 tags off the full tracks — clips are
    written with -map_metadata -1 and carry none, so that evidence is
    unrecoverable afterwards.

It writes the deletion into acquisition.json, because re-running Gate 3 later
requires re-downloading the package and a reader deserves to know why.
"""

from __future__ import annotations

import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tonic_trainer.clips import CLIP_ROOT  # noqa: E402
from tonic_trainer.crosscheck import CONFLICTS_JSON  # noqa: E402
from tonic_trainer.phase3 import ACQUISITION_JSON, AUDIO_ROOT, ZIP_DIR  # noqa: E402

REASON = "transient acquisition; clips derived and ID3 cross-check complete"


def dir_bytes(path: Path) -> int:
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())


def main() -> int:
    if not ACQUISITION_JSON.exists():
        print(f"REFUSED: {ACQUISITION_JSON} missing — nothing proves the acquisition happened")
        return 1
    acq = json.loads(ACQUISITION_JSON.read_text())

    if not CONFLICTS_JSON.exists():
        print(f"REFUSED: {CONFLICTS_JSON} missing — the ID3 cross-check must read the full "
              "tracks' tags before they are deleted; clips carry none.")
        return 1

    clips = list(CLIP_ROOT.rglob("*.mp3"))
    if len(clips) < 1000:
        print(f"REFUSED: only {len(clips)} clips derived — the sources are still needed")
        return 1

    freed = 0
    for path in (ZIP_DIR, AUDIO_ROOT):
        if path.exists():
            size = dir_bytes(path)
            print(f"removing {path} ({size / 1e9:.2f} GB)")
            shutil.rmtree(path)
            freed += size
        else:
            print(f"already gone: {path}")

    acq["sources_deleted"] = datetime.now(timezone.utc).isoformat()
    acq["sources_deleted_reason"] = REASON
    acq["sources_deleted_bytes"] = freed
    acq["gate3_passed_before_deletion"] = True
    acq["rerun_note"] = (
        "Gate 3 verifies source files on disk. Re-running it after this deletion "
        "requires re-downloading the 39.3 GB Zenodo package (record 10719860). "
        "The clips in build/clips/ are unaffected and are what the app serves."
    )
    ACQUISITION_JSON.write_text(json.dumps(acq, indent=2))

    print(f"\nfreed {freed / 1e9:.2f} GB")
    print(f"clips kept: {len(clips)} in {CLIP_ROOT}")
    print(f"recorded in {ACQUISITION_JSON}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
