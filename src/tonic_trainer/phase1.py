"""Phase 1 — fetch keys.csv and write build/keys_normalized.parquet."""

from __future__ import annotations

from .ingest import fetch_keys_csv, load_keys_raw
from .normalize import normalize_keys
from .paths import BUILD

KEYS_PARQUET = BUILD / "keys_normalized.parquet"


def build() -> None:
    path = fetch_keys_csv()
    raw = load_keys_raw(path)
    norm = normalize_keys(raw)
    norm.to_parquet(KEYS_PARQUET, index=False)

    print(f"source        : {path}")
    print(f"rows          : {len(norm)}")
    print(f"distinct keys : {norm.groupby(['tonic_pc', 'mode']).ngroups}")
    print(f"null spotify  : {int(norm['spotify_uri'].isna().sum())}")
    print(f"track_id range: {int(norm['track_id'].min())} .. {int(norm['track_id'].max())}")
    print(f"written       : {KEYS_PARQUET}")


if __name__ == "__main__":
    build()
