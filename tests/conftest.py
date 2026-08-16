"""Shared fixtures.

The server tests run against a synthetic manifest and two generated mp3s rather
than the real corpus: they must be runnable and deterministic before (and
independently of) the 39 GB acquisition, and they exercise exactly the same code
paths.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tonic_trainer.normalize import display_label

GENRES = ["Rock", "Folk", "Pop", "Electronic", "Ungenred"]


@pytest.fixture(scope="session")
def clip_dir(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("clips")
    (root / "000").mkdir()
    for name, freq in (("000001.mp3", 220), ("000002.mp3", 330)):
        out = root / "000" / name
        subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
             "-i", f"sine=frequency={freq}:duration=30", "-ac", "1", "-ar", "44100",
             "-b:a", "128k", str(out)],
            check=True,
        )
    return root


@pytest.fixture(scope="session")
def manifest(clip_dir) -> list[dict]:
    # 400 entries, not a handful: "50 calls return >= 40 distinct ids" is a
    # birthday-problem check. A 48-puzzle pool cannot pass it even with perfect
    # randomness (expected distinct ~31), so a small fixture would test the
    # fixture rather than the sampling. The real served pool is ~3600.
    entries = []
    for i in range(400):
        pc = i % 12
        mode = "major" if i % 2 == 0 else "minor"
        genre = GENRES[i % len(GENRES)]
        difficulty = (i % 3) + 1     # computed per song upstream; 1/2/3 here
        entries.append(
            {
                "id": f"fma-{i:06d}",
                "audio_path": f"000/00000{1 if i % 2 else 2}.mp3",
                "tonic_pc": pc,
                "mode": mode,
                "key_display": display_label(pc, mode),
                "title": f"Test Track {i}",
                "artist": f"Test Artist {i % 7}",
                "license": "CC BY-SA 4.0",
                "license_canonical": "CC BY-SA",
                "genre": genre,
                "difficulty": difficulty,
            }
        )
    return entries


@pytest.fixture()
def client(manifest, clip_dir, monkeypatch):
    from fastapi.testclient import TestClient

    from tonic_trainer import server

    monkeypatch.setattr(server, "CLIP_ROOT", clip_dir)
    app = server.create_app(manifest)
    return TestClient(app)
