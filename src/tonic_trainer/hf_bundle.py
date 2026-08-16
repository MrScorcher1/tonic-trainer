"""Phase 7 — prepare the Hugging Face upload bundle. Never pushes.

Publication is a legal judgment call and belongs to the user (SPEC §4), so this
builds and describes the bundle and stops there. The push is a command the user
runs with their own credentials; the exact form is written into UPLOAD.md,
verified against `hf upload --help` rather than recalled.

Dry-run is the default: it prints every file that would be uploaded with its
license, and writes nothing until `--confirm`.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import sys
from pathlib import Path

from .clips import CLIP_ROOT
from .crosscheck import CONFLICTS_JSON, conflicted_ids
from .manifest import load_manifest
from .paths import BUILD

# TT_BUNDLE_DIR lets a gate exercise the dry run in isolation instead of
# depending on the real bundle not existing yet.
BUNDLE = Path(os.environ.get("TT_BUNDLE_DIR", str(BUILD / "hf_upload")))
REPO_ID = "MrScorcher1/tonic-trainer"
RESOLVE_BASE = f"https://huggingface.co/datasets/{REPO_ID}/resolve/main/clips"

ANSWER_FIELDS = ("tonic_pc", "mode", "key_display")


def public_entries(entries: list[dict]) -> list[dict]:
    """The manifest minus the answers, so they are not trivially scrapeable."""
    out = []
    for e in entries:
        public = {k: v for k, v in e.items() if k not in ANSWER_FIELDS}
        public["audio_url"] = f"{RESOLVE_BASE}/{e['audio_path']}"
        out.append(public)
    leaked = [f for f in ANSWER_FIELDS if any(f in json.dumps(p) for p in out)]
    if leaked:
        raise ValueError(f"public puzzles still contain answer fields: {leaked}")
    return out


def dataset_card(entries: list[dict], conflicts: dict | None) -> str:
    licenses: dict[str, int] = {}
    for e in entries:
        licenses[e["license_canonical"]] = licenses.get(e["license_canonical"], 0) + 1
    rows = "\n".join(f"| {name} | {count} |" for name, count in sorted(licenses.items()))
    checked = (conflicts or {}).get("checked", 0)
    excluded = (conflicts or {}).get("excluded", 0)

    return f"""---
license: other
license_name: per-track-creative-commons
task_categories:
- audio-classification
tags:
- music
- key-detection
- ear-training
- creative-commons
---

# Tonic Trainer clips

{len(entries)} thirty-second music clips with human-made key annotations, for
ear training. Each clip is the **opening** 30 seconds of a Free Music Archive
track, re-encoded to mono 128 kbps.

## Licensing — read this first

**The audio is not ours and is not uniformly licensed.** Every clip is
redistributed under the original Creative Commons terms of its own track, which
are recorded per file in `attribution.csv`. Nothing here is public domain by
default, and this dataset card's own license field cannot override a track's.

* Tracks under any **NoDerivatives** licence are excluded — slicing a clip out
  of one would be a derivative work.
* NonCommercial tracks are included and flagged; respect the NC terms.
* **Attribution is mandatory.** Artist, title and licence for every clip are in
  `attribution.csv`, and any application built on this must display them.

| licence | clips |
|---|---|
{rows}

Licence resolution was cross-checked against {checked} tracks using FMA's
`tracks.csv` (authoritative) and the mp3s' embedded ID3 tags; {excluded} were
excluded on conflict or an ND signal. See `license_conflicts.json`.

## Contents

| file | what it is |
|---|---|
| `clips/NNN/NNNNNN.mp3` | 30 s mono clip, named by FMA track id |
| `attribution.csv` | id, title, artist, licence, genre — one row per clip |
| `puzzles_public.json` | clip metadata **without** the answers |
| `license_conflicts.json` | licence cross-check output |

`puzzles_public.json` deliberately omits `tonic_pc`, `mode` and `key_display`
so the answers are not trivially scrapeable by anyone playing the game.

## Sources

* Audio: [Free Music Archive](https://freemusicarchive.org), via the
  [FMAK package](https://zenodo.org/records/10719860) (CC BY 4.0 for the
  compilation; per-track licences govern the audio).
* Key annotations: [`fma_keys`](https://github.com/stellaywong/fma_keys) —
  human-annotated, ISMIR 2023 late-breaking demo by Stella Wong. Single
  annotator, lightly reviewed; labels are good but not cross-checked.

## Known limitations

* Clips are openings, so the material is whatever the track starts with.
* The key distribution is lopsided toward C major and A minor.
* FMA skews independent and electronic; some tracks have no clear tonal centre.
"""


def upload_instructions(n_files: int, total_bytes: float) -> str:
    return f"""# Pushing this bundle

Run by **you**, with your own Hugging Face credentials. Nothing in this repo
pushes anything.

The CLI is `hf` — `huggingface-cli` is gone. Verified against `hf upload --help`
(huggingface_hub 1.27.0) on this machine:

```bash
hf auth login                      # once
hf upload {REPO_ID} build/hf_upload . --repo-type dataset \\
   --commit-message "Tonic Trainer clips: {n_files} clips with attribution"
```

That uploads {n_files} files, about {total_bytes / 1e9:.2f} GB.

## After the push: check CORS before switching the app to remote audio

Neither the build agent nor the strategist can reach huggingface.co, so this is
the one thing only you can test:

```bash
curl -sSI -H "Origin: http://localhost:8000" \\
  "{RESOLVE_BASE}/000/000010.mp3" | grep -i "access-control-allow-origin"
```

* **Header present** → the browser can fetch clips directly:

  ```bash
  TT_AUDIO_BASE="{RESOLVE_BASE}" .venv/bin/python -m tonic_trainer.server
  ```

* **Header absent** (the likelier outcome — HF `resolve/` responses are not
  reliably CORS-enabled, and the `cas-bridge.xethub.hf.co` redirect target has
  been reported failing preflight) → use proxy mode, which does not care:

  ```bash
  .venv/bin/python -m tonic_trainer.server \\
     --audio-base "{RESOLVE_BASE}" --audio-proxy
  ```

  The server fetches each clip once, caches it, and serves it same-origin with
  Range/206 intact. Same result, one extra hop.

Either way the local clips remain the default if you set neither flag.
"""


def build(*, confirm: bool) -> dict:
    entries = load_manifest()
    conflicts = json.loads(CONFLICTS_JSON.read_text()) if CONFLICTS_JSON.exists() else None
    if conflicts is None:
        raise FileNotFoundError(
            f"{CONFLICTS_JSON} missing — run the licence cross-check before preparing "
            "an upload. It needs the full tracks' ID3 tags, so it cannot be run later."
        )
    # Every genuine disagreement is excluded from publication, not just the ND
    # ones: redistribution is where an NC-vs-not conflict starts to matter.
    excluded = conflicted_ids(conflicts)
    entries = [e for e in entries if e["id"] not in excluded]
    print(f"licence cross-check excludes {len(excluded)} ids from publication; "
          f"{len(entries)} clips remain")
    files = [(e, CLIP_ROOT / e["audio_path"]) for e in entries]
    missing = [e["id"] for e, p in files if not p.exists()]
    if missing:
        raise FileNotFoundError(f"{len(missing)} manifest clips are missing on disk: {missing[:5]}")
    total = sum(p.stat().st_size for _e, p in files)

    print(f"{'file':<28} {'bytes':>9}  {'licence':<16} artist — title")
    print("-" * 100)
    for e, p in files[:25]:
        print(f"clips/{e['audio_path']:<22} {p.stat().st_size:>9}  "
              f"{e['license_canonical']:<16} {e['artist'][:28]} — {e['title'][:34]}")
    if len(files) > 25:
        print(f"... and {len(files) - 25} more (full list in attribution.csv)")
    print("-" * 100)
    print(f"{len(files)} clips, {total / 1e9:.2f} GB, plus attribution.csv, "
          f"puzzles_public.json, license_conflicts.json and README.md")

    if not confirm:
        print("\nDRY RUN — nothing written. Re-run with --confirm to materialise the bundle.")
        print("Even then, this never pushes: the push is your command, in UPLOAD.md.")
        return {"files": len(files), "bytes": total, "written": False}

    BUNDLE.mkdir(parents=True, exist_ok=True)
    for e, src in files:
        dst = BUNDLE / "clips" / e["audio_path"]
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.exists():
            continue
        try:
            os.link(src, dst)  # same filesystem: no second copy of the corpus
        except OSError:
            shutil.copy2(src, dst)

    with (BUNDLE / "attribution.csv").open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["id", "file", "title", "artist", "license", "license_canonical",
                         "genre", "difficulty"])
        for e, _p in files:
            writer.writerow([e["id"], f"clips/{e['audio_path']}", e["title"], e["artist"],
                             e["license"], e["license_canonical"], e["genre"]])

    (BUNDLE / "puzzles_public.json").write_text(json.dumps(public_entries(entries), indent=1))
    (BUNDLE / "README.md").write_text(dataset_card(entries, conflicts))
    (BUNDLE / "UPLOAD.md").write_text(upload_instructions(len(files), total))
    if CONFLICTS_JSON.exists():
        shutil.copy2(CONFLICTS_JSON, BUNDLE / "license_conflicts.json")

    print(f"\nbundle written to {BUNDLE}")
    print("It has NOT been pushed. See UPLOAD.md for the command that does that.")
    return {"files": len(files), "bytes": total, "written": True}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Prepare the Hugging Face upload bundle")
    parser.add_argument("--confirm", action="store_true",
                        help="materialise the bundle (still does not push)")
    args = parser.parse_args(argv)
    build(confirm=args.confirm)
    return 0


if __name__ == "__main__":
    sys.exit(main())
