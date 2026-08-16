"""Derive the 30-second loops the app serves from FMAK's full-length tracks.

SPEC §1.3 assumed the FMAK package shipped 30-second clips cut from the middle
of each track. It does not — it ships full-length audio (~210 s, ~264 kbps
median), so the clip is produced here rather than assumed.

**Position: the opening.** The spec's "you never hear a song opening" entry in
§3 was an accepted *limitation* of FMA's packaging, not a design choice, and
holding full tracks makes it optional. The user chose openings (2026-08-16):
openings are where a tonic is most clearly established, which is the whole skill
being trained. `CLIP_POSITION` still supports "middle" if that is ever revisited.

Re-encoded rather than stream-copied: an MP3 frame-boundary copy leaves encoder
delay/padding that makes `loop = true` audibly gap. Re-encoding to a single
clean file, mono, 128 kbps, keeps the loop seamless and the transfer small
(~0.5 MB per puzzle instead of ~7 MB).
"""

from __future__ import annotations

import json
import subprocess
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from mutagen.mp3 import MP3

from .paths import BUILD

CLIP_ROOT = BUILD / "clips"
CLIP_SECONDS = 30.0
CLIP_POSITION = "start"  # "start" (user's choice) | "middle"
MIN_SOURCE_SECONDS = 35.0
CLIP_BITRATE = "128k"
CLIP_SAMPLE_RATE = "44100"
CLIP_REPORT = BUILD / "clips.json"


@dataclass(frozen=True)
class ClipResult:
    track_id: int
    rel_path: str | None
    ok: bool
    reason: str
    source_seconds: float | None = None
    clip_seconds: float | None = None


def probe_duration(path: Path) -> float:
    """Duration in seconds via mutagen. Raises on an unreadable file."""
    audio = MP3(str(path))
    length = float(audio.info.length)
    if length <= 0:
        raise ValueError(f"{path}: mutagen reports a non-positive duration {length}")
    return length


def clip_one(args: tuple[int, str, str]) -> ClipResult:
    """Cut one 30 s clip from the middle of a source track."""
    track_id, source_str, rel_path = args
    source = Path(source_str)
    out = CLIP_ROOT / rel_path

    if not source.exists():
        return ClipResult(track_id, None, False, "source file missing")
    if source.stat().st_size < 10_240:
        return ClipResult(track_id, None, False, f"source is {source.stat().st_size} bytes (<10 KB)")

    try:
        duration = probe_duration(source)
    except Exception as exc:  # noqa: BLE001 — a bad file is dropped with a logged reason
        return ClipResult(track_id, None, False, f"mutagen could not read the source: {exc}")

    if duration < MIN_SOURCE_SECONDS:
        return ClipResult(track_id, None, False,
                          f"source is only {duration:.1f}s (<{MIN_SOURCE_SECONDS}s)", duration)

    start = 0.0 if CLIP_POSITION == "start" else max(0.0, (duration - CLIP_SECONDS) / 2.0)

    if not out.exists() or out.stat().st_size == 0:
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_suffix(".part.mp3")
        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
            "-ss", f"{start:.3f}", "-t", f"{CLIP_SECONDS:.3f}", "-i", str(source),
            "-vn", "-ac", "1", "-ar", CLIP_SAMPLE_RATE, "-b:a", CLIP_BITRATE,
            "-map_metadata", "-1", str(tmp),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            tmp.unlink(missing_ok=True)
            return ClipResult(track_id, None, False,
                              f"ffmpeg failed ({proc.returncode}): {proc.stderr.strip()[:200]}", duration)
        tmp.replace(out)

    try:
        clip_len = probe_duration(out)
    except Exception as exc:  # noqa: BLE001
        return ClipResult(track_id, None, False, f"clip is unreadable: {exc}", duration)

    if not (25.0 <= clip_len <= 35.0):
        return ClipResult(track_id, None, False,
                          f"clip duration {clip_len:.1f}s outside 25-35s", duration, clip_len)

    return ClipResult(track_id, rel_path, True, "ok", duration, clip_len)


def build_clips(jobs: list[tuple[int, str, str]], workers: int = 8) -> list[ClipResult]:
    CLIP_ROOT.mkdir(parents=True, exist_ok=True)
    results: list[ClipResult] = []
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for i, res in enumerate(pool.map(clip_one, jobs, chunksize=8), start=1):
            results.append(res)
            if i % 250 == 0:
                ok = sum(r.ok for r in results)
                print(f"  clips: {i}/{len(jobs)} done, {ok} ok", flush=True)
    return results


def write_report(results: list[ClipResult]) -> dict:
    failures = [r for r in results if not r.ok]
    report = {
        "clip_seconds": CLIP_SECONDS,
        "clip_position": CLIP_POSITION,
        "bitrate": CLIP_BITRATE,
        "sample_rate": CLIP_SAMPLE_RATE,
        "channels": 1,
        "attempted": len(results),
        "ok": len(results) - len(failures),
        "dropped": len(failures),
        "drop_reasons": {},
        "dropped_track_ids": [r.track_id for r in failures],
    }
    for r in failures:
        # Bucket by reason prefix so the report stays readable.
        key = r.reason.split(":")[0].split("(")[0].strip()
        report["drop_reasons"][key] = report["drop_reasons"].get(key, 0) + 1
    CLIP_REPORT.write_text(json.dumps(report, indent=2))
    return report
