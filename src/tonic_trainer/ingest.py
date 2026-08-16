"""Fetching and raw parsing of the source datasets.

Everything here fails loudly: a short download, a missing file, or a CSV whose
shape differs from the documented one raises rather than returning something
partial. Silent coercion of bad rows is the primary failure mode of this
project (SPEC §0.3).
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pandas as pd
import requests

from .paths import DATA

KEYS_CSV_URL = "https://raw.githubusercontent.com/stellaywong/fma_keys/master/keys.csv"
KEYS_CSV = DATA / "keys.csv"

FMA_METADATA_URL = "https://os.unil.cloud.switch.ch/fma/fma_metadata.zip"
FMA_METADATA_ZIP = DATA / "fma_metadata.zip"
# The expected SHA1 is read from mdeff/fma's README at verification time rather
# than hardcoded from memory — see filter.published_fma_sha1().
FMA_README_URL = "https://raw.githubusercontent.com/mdeff/fma/master/README.md"

# Verified facts about keys.csv (SPEC §1.1). Asserted, never adapted to.
EXPECTED_ROWS = 5489
EXPECTED_DISTINCT_KEYS = 24
EXPECTED_NULL_SPOTIFY = 232
EXPECTED_TRACK_ID_MIN = 10
EXPECTED_TRACK_ID_MAX = 124911


def download(url: str, dest: Path, *, timeout: int = 300) -> Path:
    """Stream a URL to disk. Raises on any non-200 or truncated body."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    with requests.get(url, stream=True, timeout=timeout) as resp:
        resp.raise_for_status()
        declared = resp.headers.get("Content-Length")
        written = 0
        tmp = dest.with_suffix(dest.suffix + ".part")
        with tmp.open("wb") as fh:
            for chunk in resp.iter_content(chunk_size=1 << 20):
                fh.write(chunk)
                written += len(chunk)
        if declared is not None and written != int(declared):
            tmp.unlink()
            raise IOError(f"{url}: truncated download, got {written} of {declared} bytes")
        tmp.replace(dest)
    return dest


def sha1_of(path: Path, *, chunk: int = 1 << 22) -> str:
    h = hashlib.sha1()
    with path.open("rb") as fh:
        while block := fh.read(chunk):
            h.update(block)
    return h.hexdigest()


def fetch_keys_csv(*, force: bool = False) -> Path:
    if force or not KEYS_CSV.exists():
        download(KEYS_CSV_URL, KEYS_CSV)
    return KEYS_CSV


def load_keys_raw(path: Path | None = None) -> pd.DataFrame:
    """Read keys.csv, handling its two header rows.

    The file was written from a pandas MultiIndex, so line 1 is
    ``,spotify,key_and_mode`` and line 2 is the real header. A default
    ``read_csv`` turns line 2 into a data row (SPEC §1.1).
    """
    path = path or fetch_keys_csv()
    df = pd.read_csv(path, header=[0, 1], index_col=0)
    df.columns = [second for _first, second in df.columns]
    df.index.name = "track_id"
    df = df.reset_index()

    missing = {"track_id", "spotify_uri", "key_and_mode"} - set(df.columns)
    if missing:
        raise ValueError(f"keys.csv is missing expected columns: {sorted(missing)}")

    if len(df) != EXPECTED_ROWS:
        raise ValueError(f"keys.csv row count is {len(df)}, expected {EXPECTED_ROWS}")

    if df["track_id"].isna().any():
        raise ValueError("keys.csv has rows with a null track_id")
    df["track_id"] = df["track_id"].astype(int)

    if df["key_and_mode"].isna().any():
        n = int(df["key_and_mode"].isna().sum())
        raise ValueError(f"keys.csv has {n} rows with no key_and_mode value")

    return df
