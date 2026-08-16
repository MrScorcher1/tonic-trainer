"""Serve the real app over a synthetic corpus, for running the e2e suite early.

Gate 6 itself runs against the real manifest. This exists so the browser-level
invariants can be exercised before the 39 GB acquisition finishes — same server,
same page, generated clips.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

import uvicorn

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from tonic_trainer import server  # noqa: E402
from tonic_trainer.normalize import display_label  # noqa: E402

GENRES = ["Rock", "Folk", "Pop", "Electronic", None]


def build_fixture(root: Path) -> list[dict]:
    (root / "000").mkdir(parents=True, exist_ok=True)
    for name, freqs in (
        ("000001.mp3", [261.63, 329.63, 392.00]),
        ("000002.mp3", [440.00, 523.25, 659.25]),
    ):
        out = root / "000" / name
        if out.exists():
            continue
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"]
        for f in freqs:
            cmd += ["-f", "lavfi", "-i", f"sine=frequency={f}:duration=30"]
        cmd += ["-filter_complex", f"amix=inputs={len(freqs)}", "-ac", "1", "-b:a", "128k", str(out)]
        subprocess.run(cmd, check=True)

    entries = []
    for i in range(400):
        pc, mode = i % 12, ("major" if i % 2 == 0 else "minor")
        genre = GENRES[i % len(GENRES)]
        difficulty = (
            "untagged" if genre is None
            else ("tier1" if mode == "major" else "tier2") if genre in ("Rock", "Folk", "Pop")
            else "tier3"
        )
        entries.append({
            "id": f"fma-{i:06d}",
            "audio_path": f"000/00000{1 if i % 2 else 2}.mp3",
            "tonic_pc": pc, "mode": mode, "key_display": display_label(pc, mode),
            "title": f"Fixture Track {i}", "artist": f"Fixture Artist {i % 7}",
            "license": "CC BY-SA 4.0", "license_canonical": "CC BY-SA",
            "genre_top": genre, "difficulty": difficulty,
        })
    return entries


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8099
    root = Path(tempfile.gettempdir()) / "tt-e2e-clips"
    entries = build_fixture(root)
    server.CLIP_ROOT = root
    uvicorn.run(server.create_app(entries), host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
