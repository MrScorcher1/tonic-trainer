"""Phase 3 — acquire the FMAK audio package from Zenodo record 10719860.

Resumable download of the 10 range-zips (~39 GB), MD5 verification against the
checksums published *in the record* (the record publishes MD5, not SHA1 — read,
never assumed), extraction of the keyed track ids, and a written acquisition
record at `build/acquisition.json`.

**Finding that contradicts SPEC §1.3.** The spec states the package holds "the
same 30-second FMA clips, cut from the middle of each track". It does not: the
FMAK zips ship *full-length* tracks (median 7.6 MB, ~210 s, ~264 kbps). That is
why this phase has a clip-derivation step the original spec did not need — the
30-second loop the app serves is cut from the middle of each full track here,
preserving both the spec's stated clip semantics and Gate 3's 25-35 s duration
requirement, which no unmodified FMAK file could satisfy.
"""

from __future__ import annotations

import hashlib
import json
import os
import zipfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import requests

from .paths import BUILD, DATA

ZENODO_RECORD = 10719860
ZENODO_API = f"https://zenodo.org/api/records/{ZENODO_RECORD}"
RECORD_JSON = DATA / f"zenodo_{ZENODO_RECORD}.json"
ZIP_DIR = DATA / "fmak_zips"
AUDIO_ROOT = DATA / "fmak_audio"
ACQUISITION_JSON = BUILD / "acquisition.json"


@dataclass(frozen=True)
class RemoteFile:
    key: str
    size: int
    checksum: str  # "md5:...."
    url: str

    @property
    def algo(self) -> str:
        return self.checksum.split(":", 1)[0]

    @property
    def digest(self) -> str:
        return self.checksum.split(":", 1)[1]


def fetch_record() -> dict:
    if not RECORD_JSON.exists():
        resp = requests.get(ZENODO_API, timeout=120)
        resp.raise_for_status()
        RECORD_JSON.write_text(resp.text)
    return json.loads(RECORD_JSON.read_text())


def remote_files(record: dict | None = None) -> list[RemoteFile]:
    record = record or fetch_record()
    files = [
        RemoteFile(f["key"], int(f["size"]), f["checksum"], f["links"]["self"])
        for f in record["files"]
    ]
    if not files:
        raise ValueError("Zenodo record lists no files")
    return files


def digest_of(path: Path, algo: str, *, chunk: int = 1 << 22) -> str:
    h = hashlib.new(algo)
    with path.open("rb") as fh:
        while block := fh.read(chunk):
            h.update(block)
    return h.hexdigest()


def download_resumable(rf: RemoteFile, dest: Path, *, attempts: int = 20) -> Path:
    """Download with HTTP Range resume. Raises if the file never completes."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(1, attempts + 1):
        have = dest.stat().st_size if dest.exists() else 0
        if have == rf.size:
            return dest
        if have > rf.size:
            raise IOError(f"{dest} is larger ({have}) than the published size ({rf.size})")

        headers = {"Range": f"bytes={have}-"} if have else {}
        try:
            with requests.get(rf.url, headers=headers, stream=True, timeout=(30, 300)) as resp:
                if have and resp.status_code != 206:
                    raise IOError(f"resume not honoured for {rf.key}: HTTP {resp.status_code}")
                resp.raise_for_status()
                mode = "ab" if have else "wb"
                with dest.open(mode) as fh:
                    for chunk in resp.iter_content(chunk_size=1 << 22):
                        fh.write(chunk)
        except (requests.RequestException, IOError) as exc:
            got = dest.stat().st_size if dest.exists() else 0
            print(f"  {rf.key}: attempt {attempt} interrupted at {got}/{rf.size} — {exc}", flush=True)
            if attempt == attempts:
                raise
            continue

    have = dest.stat().st_size if dest.exists() else 0
    if have != rf.size:
        raise IOError(f"{rf.key}: stopped at {have} of {rf.size} bytes after {attempts} attempts")
    return dest


def acquire_one(rf: RemoteFile) -> dict:
    dest = ZIP_DIR / rf.key
    download_resumable(rf, dest)
    actual = digest_of(dest, rf.algo)
    ok = actual == rf.digest
    print(f"  {rf.key}: {rf.size} bytes, {rf.algo} {'OK' if ok else 'MISMATCH'}", flush=True)
    if not ok:
        raise ValueError(
            f"{rf.key}: {rf.algo} mismatch — got {actual}, record publishes {rf.digest}"
        )
    return {"file": rf.key, "size": rf.size, "algo": rf.algo,
            "published": rf.digest, "actual": actual, "verified": ok}


def download_all(workers: int = 3) -> list[dict]:
    files = remote_files()
    ZIP_DIR.mkdir(parents=True, exist_ok=True)
    print(f"acquiring {len(files)} files from Zenodo record {ZENODO_RECORD} "
          f"({sum(f.size for f in files) / 1e9:.1f} GB) with {workers} streams", flush=True)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(acquire_one, files))


def extract_all(keyed_ids: set[int]) -> dict[int, str]:
    """Extract the keyed track ids from every zip. Returns track_id -> relpath."""
    AUDIO_ROOT.mkdir(parents=True, exist_ok=True)
    found: dict[int, str] = {}
    for zip_path in sorted(ZIP_DIR.glob("*.zip")):
        with zipfile.ZipFile(zip_path) as zf:
            members = [n for n in zf.namelist() if n.endswith(".mp3")]
            wanted = []
            for name in members:
                stem = Path(name).stem
                if not stem.isdigit():
                    raise ValueError(f"{zip_path.name}: unexpected member name {name!r}")
                tid = int(stem)
                if tid in keyed_ids:
                    wanted.append((tid, name))
            print(f"  {zip_path.name}: {len(members)} mp3s, {len(wanted)} keyed", flush=True)
            for tid, name in wanted:
                out = AUDIO_ROOT / name
                if not out.exists() or out.stat().st_size == 0:
                    out.parent.mkdir(parents=True, exist_ok=True)
                    with zf.open(name) as src, out.open("wb") as dst:
                        while block := src.read(1 << 22):
                            dst.write(block)
                found[tid] = name
    return found


def write_acquisition(checksums: list[dict], found: dict[int, str], keyed_ids: set[int],
                      *, source: str, notes: list[str]) -> dict:
    missing = sorted(keyed_ids - set(found))
    record = {
        "source": source,
        "zenodo_record": ZENODO_RECORD,
        "fallback_used": False,
        "range_probe": None,
        "checksums": checksums,
        "annotated_tracks_expected": len(keyed_ids),
        "annotated_tracks_obtained": len(found),
        "missing_track_ids": missing,
        "audio_root": str(AUDIO_ROOT),
        "notes": notes,
    }
    ACQUISITION_JSON.write_text(json.dumps(record, indent=2))
    return record


def main() -> None:
    import pandas as pd

    from .phase1 import KEYS_PARQUET

    keyed_ids = set(pd.read_parquet(KEYS_PARQUET)["track_id"].astype(int))
    checksums = download_all(workers=int(os.environ.get("FMAK_STREAMS", "3")))
    found = extract_all(keyed_ids)
    record = write_acquisition(
        checksums,
        found,
        keyed_ids,
        source=f"Zenodo record {ZENODO_RECORD} (FMAK audio package)",
        notes=[
            "FMAK zips contain FULL-LENGTH tracks (median ~7.6 MB, ~210 s), not the "
            "30-second clips SPEC §1.3 describes. The served 30 s loop is cut from the "
            "middle of each track in phase3_clips.py.",
        ],
    )
    print(f"\nobtained {record['annotated_tracks_obtained']} of {len(keyed_ids)} annotated tracks")
    print(f"missing  {len(record['missing_track_ids'])}")
    print(f"written  {ACQUISITION_JSON}")


if __name__ == "__main__":
    main()
