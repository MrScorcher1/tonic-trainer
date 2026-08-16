"""Phase 2 — join FMA metadata onto the normalized keys, filter by license.

Downloads `fma_metadata.zip` (metadata only — no audio, SPEC §0.4), verifies it
against the SHA1 published in mdeff/fma's README, extracts `tracks.csv`, joins
on track_id, applies the license filter, and writes
`build/keys_metadata.parquet` plus a printed coverage report.
"""

from __future__ import annotations

import zipfile

import pandas as pd
import requests

from .filter import apply_license_filter, published_fma_sha1
from .ingest import (
    FMA_METADATA_URL,
    FMA_METADATA_ZIP,
    FMA_README_URL,
    download,
    sha1_of,
)
from .paths import BUILD, DATA
from .phase1 import KEYS_PARQUET

TRACKS_CSV = DATA / "fma_metadata" / "tracks.csv"
JOINED_PARQUET = BUILD / "keys_metadata.parquet"


def ensure_metadata() -> None:
    if not FMA_METADATA_ZIP.exists():
        print(f"downloading {FMA_METADATA_URL} ...")
        download(FMA_METADATA_URL, FMA_METADATA_ZIP, timeout=1800)

    readme = requests.get(FMA_README_URL, timeout=120)
    readme.raise_for_status()
    expected = published_fma_sha1("fma_metadata.zip", readme.text)
    actual = sha1_of(FMA_METADATA_ZIP)
    if actual != expected:
        raise ValueError(
            f"fma_metadata.zip SHA1 mismatch: got {actual}, README publishes {expected}"
        )
    print(f"fma_metadata.zip SHA1 {actual} matches the published value")

    if not TRACKS_CSV.exists():
        with zipfile.ZipFile(FMA_METADATA_ZIP) as zf:
            zf.extract("fma_metadata/tracks.csv", DATA)
            zf.extract("fma_metadata/genres.csv", DATA)


def load_tracks() -> pd.DataFrame:
    """tracks.csv has a two-level column header and a separate index-name row."""
    tracks = pd.read_csv(TRACKS_CSV, index_col=0, header=[0, 1], low_memory=False)
    if tracks.index.name != "track_id":
        raise ValueError(f"tracks.csv index is {tracks.index.name!r}, expected 'track_id'")
    flat = pd.DataFrame(
        {
            "track_id": tracks.index.astype(int),
            "title": tracks[("track", "title")].values,
            "artist": tracks[("artist", "name")].values,
            "license": tracks[("track", "license")].values,
            "genre_top": tracks[("track", "genre_top")].values,
            "duration": tracks[("track", "duration")].values,
        }
    )
    if not flat["track_id"].is_unique:
        raise ValueError("tracks.csv has duplicate track_ids")
    return flat


def build() -> pd.DataFrame:
    ensure_metadata()
    keys = pd.read_parquet(KEYS_PARQUET)
    tracks = load_tracks()

    joined = keys.merge(tracks, on="track_id", how="inner", validate="one_to_one")
    joined = apply_license_filter(joined)

    def nonempty(col: pd.Series) -> pd.Series:
        return col.notna() & (col.astype(str).str.strip() != "")

    joined["has_attribution"] = nonempty(joined["title"]) & nonempty(joined["artist"])
    joined["usable"] = joined["license_allowed"] & joined["has_attribution"]
    joined.to_parquet(JOINED_PARQUET, index=False)

    report(keys, joined)
    return joined


def report(keys: pd.DataFrame, joined: pd.DataFrame) -> None:
    usable = joined[joined["usable"]]
    print()
    print("=== Phase 2 — coverage report ===")
    print(f"annotated tracks (keys.csv)            : {len(keys)}")
    print(f"  with an FMA metadata row             : {len(joined)}")
    print(f"  missing from FMA metadata            : {len(keys) - len(joined)}")
    print(f"  passing the license filter           : {int(joined['license_allowed'].sum())}")
    print(f"  with title + artist (attributable)   : {int(joined['has_attribution'].sum())}")
    print(f"  USABLE (licensed + attributable)     : {len(usable)}")
    print(f"  of those, NC-flagged (allowed)       : {int(usable['license_is_nc'].sum())}")
    print(f"  with a genre_top label               : {int(usable['genre_top'].notna().sum())}")

    print()
    print("license breakdown (annotated set):")
    lic = (
        joined.groupby(["license_canonical", "license_allowed"])
        .size()
        .reset_index(name="n")
        .sort_values("n", ascending=False)
    )
    for _, row in lic.iterrows():
        flag = "allow" if row["license_allowed"] else "EXCLUDE"
        print(f"  {row['license_canonical']:<30} {row['n']:>5}  {flag}")

    excluded = joined[~joined["license_allowed"]]
    if len(excluded):
        print()
        print("exclusion reasons:")
        for reason, n in excluded["license_reason"].value_counts().items():
            print(f"  {n:>5}  {reason}")

    print()
    print("genre breakdown (usable tracks):")
    genres = usable["genre_top"].value_counts(dropna=False)
    for genre, n in genres.items():
        label = "(untagged)" if pd.isna(genre) else str(genre)
        print(f"  {label:<22} {n:>5}")

    print()
    print(f"written: {JOINED_PARQUET}")


if __name__ == "__main__":
    build()
