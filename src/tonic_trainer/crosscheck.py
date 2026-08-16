"""Phase 7 — three-source license cross-check (SPEC §Phase 7).

Sources, in order of authority:

1. ``tracks.csv``'s ``license`` field — authoritative, always present.
2. The mp3's embedded ID3 tags (``TCOP`` copyright, ``WCOP``/``WXXX`` license
   URL), read with mutagen.
3. The track's page on freemusicarchive.org — rate-limited, cached, and
   skippable with ``--skip-web-verify`` (the default here).

**Missing is not disagreement.** FMA mp3s routinely ship with empty tag fields;
auto-excluding on an absent ``TCOP`` would gut the corpus for no gain. A source
that produced nothing is no signal. Exclusion happens only on a genuine conflict
between two sources that both produced a value, on an ND result from any source,
or on source 1 being absent or unparseable.

This must run against the *source* mp3s: derived clips are written with
``-map_metadata -1``, so they carry no tags to read.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from mutagen import File as MutagenFile

from .filter import UNKNOWN, classify_license
from .paths import BUILD

CONFLICTS_JSON = BUILD / "license_conflicts.json"

# creativecommons.org/licenses/<code>/<version>/ -> canonical id
_CC_URL_RE = re.compile(
    r"creativecommons\.org/(?:licenses|publicdomain)/([a-z0-9\-]+)", re.IGNORECASE
)
_URL_CODE_MAP = {
    "by": "CC BY",
    "by-sa": "CC BY-SA",
    "by-nc": "CC BY-NC",
    "by-nc-sa": "CC BY-NC-SA",
    "by-nd": "CC BY-ND",
    "by-nc-nd": "CC BY-NC-ND",
    "zero": "CC0-1.0",
    "mark": "Public Domain",
}
ND_IDS = {"CC BY-ND", "CC BY-NC-ND"}


@dataclass
class TrackVerdict:
    track_id: int
    id: str
    source1: str | None
    source2: str | None
    source3: str | None
    resolved: str | None
    excluded: bool
    reasons: list[str] = field(default_factory=list)

    @property
    def conflicting(self) -> bool:
        seen = {s for s in (self.source1, self.source2, self.source3) if s}
        return len(seen) > 1


def canonical_from_url(url: str) -> str | None:
    match = _CC_URL_RE.search(url or "")
    if not match:
        return None
    return _URL_CODE_MAP.get(match.group(1).lower())


def license_from_tags(path: str) -> str | None:
    """Canonical license from a file's ID3 tags, or None when it says nothing."""
    audio = MutagenFile(path)
    if audio is None or not getattr(audio, "tags", None):
        return None
    tags = audio.tags

    for key in tags.keys():
        if key.startswith("WCOP") or key.startswith("WXXX"):
            frame = tags[key]
            url = getattr(frame, "url", None) or str(frame)
            canonical = canonical_from_url(url)
            if canonical:
                return canonical

    for key in tags.keys():
        if key.startswith("TCOP"):
            text = " ".join(getattr(tags[key], "text", []) or [str(tags[key])])
            canonical = canonical_from_url(text)
            if canonical:
                return canonical
            verdict = classify_license(text)
            if verdict.canonical != UNKNOWN:
                return verdict.canonical
    return None


def cross_check(entry: dict, source_path: str | None, *, skip_web_verify: bool = True) -> TrackVerdict:
    track_id = int(entry["id"].split("-")[1])
    reasons: list[str] = []

    verdict1 = classify_license(entry["license"])
    source1 = None if verdict1.canonical == UNKNOWN else verdict1.canonical
    if source1 is None:
        reasons.append("source 1 (tracks.csv) is absent or unparseable")

    source2 = license_from_tags(source_path) if source_path else None

    # Source 3 is a ToS and rate-limit question of its own; skipped by default.
    # Two sources suffice: source 1 is authoritative, source 2 corroborates.
    source3 = None
    if not skip_web_verify:
        raise NotImplementedError(
            "web verification is not implemented; run with skip_web_verify=True. "
            "Enabling it requires robots.txt compliance, a low fixed request rate "
            "and an on-disk cache, per SPEC Phase 7."
        )

    present = [s for s in (source1, source2, source3) if s]
    if any(s in ND_IDS for s in present):
        reasons.append("a source reports NoDerivatives")
    if len({*present}) > 1:
        reasons.append(f"sources disagree: {sorted(set(present))}")

    excluded = bool(reasons)
    return TrackVerdict(
        track_id=track_id, id=entry["id"], source1=source1, source2=source2,
        source3=source3, resolved=source1, excluded=excluded, reasons=reasons,
    )


def run(entries: list[dict], source_paths: dict[int, str], *,
        skip_web_verify: bool = True) -> dict:
    verdicts = [
        cross_check(e, source_paths.get(int(e["id"].split("-")[1])),
                    skip_web_verify=skip_web_verify)
        for e in entries
    ]
    conflicts = [v for v in verdicts if v.conflicting or v.excluded]
    payload = {
        "checked": len(verdicts),
        "sources_used": ["tracks.csv", "id3-tags"] + ([] if skip_web_verify else ["fma-web"]),
        "web_verify_skipped": skip_web_verify,
        "with_tag_signal": sum(1 for v in verdicts if v.source2),
        "without_tag_signal": sum(1 for v in verdicts if not v.source2),
        "excluded": sum(1 for v in verdicts if v.excluded),
        "conflicts": [
            {
                "id": v.id, "track_id": v.track_id,
                "source1_tracks_csv": v.source1, "source2_id3": v.source2,
                "source3_web": v.source3, "excluded": v.excluded, "reasons": v.reasons,
            }
            for v in conflicts
        ],
    }
    CONFLICTS_JSON.write_text(json.dumps(payload, indent=1))
    return payload
