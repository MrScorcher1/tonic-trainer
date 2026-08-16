"""Phase S1 — emit the static site's data: a manifest with no answers, and one
verifier file per puzzle.

**The answer files hold a hash, not an answer.** Each is
``sha256("<puzzle_id>:<tonic_pc>:<mode>")``. At submit the page hashes all 24
(tonic, mode) combinations with that puzzle's id and finds the match, which both
scores the guess and recovers the key to display.

**The puzzle id in the hash input is load-bearing.** Without it, all 3,322 files
collapse into a 24-entry lookup table: compute those once and the whole corpus
is readable. With it, the brute force must be re-run per puzzle. Gate S1 pins
this by asserting two puzzles sharing a key have different hashes.

**This is obfuscation, not security, and the difference matters.** It removes the
casually-legible answer — nothing meaningful appears in the network tab or in
view-source, and recovering a key requires writing code rather than reading a
value. It cannot stop anyone who reimplements the 24-way brute force, and no
client-side scheme can, because there are only 24 possible answers. Do not call
it encryption, and do not add further "protection": the answer space is the
binding constraint, so the next scheme would cost complexity and buy nothing.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

from .manifest import DEFAULT_POOL, load_manifest, served_pool
from .paths import ROOT

DOCS = ROOT / "docs"
STATIC_MANIFEST = DOCS / "manifest.json"
ANSWERS_DIR = DOCS / "answers"
CONFIG_JSON = DOCS / "config.json"

ANSWER_FIELDS = ("tonic_pc", "mode", "key_display")
HF_AUDIO_BASE = (
    "https://huggingface.co/datasets/MrScorcher1/tonic-trainer/resolve/main/clips"
)


def verifier(puzzle_id: str, tonic_pc: int, mode: str) -> str:
    """sha256("<id>:<tonic_pc>:<mode>") — the id is the salt, never omit it."""
    return hashlib.sha256(f"{puzzle_id}:{int(tonic_pc)}:{mode}".encode()).hexdigest()


def public_entry(entry: dict) -> dict:
    """A manifest entry with every answer field removed."""
    public = {k: v for k, v in entry.items() if k not in ANSWER_FIELDS}
    leaked = [f for f in ANSWER_FIELDS if f in json.dumps(public)]
    if leaked:
        raise ValueError(f"{entry['id']}: answer fields survived stripping: {leaked}")
    return public


def build(pool: tuple[str, ...] = DEFAULT_POOL) -> dict:
    entries = served_pool(load_manifest(), pool)
    if not entries:
        raise ValueError("no entries in the served pool — nothing to publish")

    DOCS.mkdir(parents=True, exist_ok=True)
    if ANSWERS_DIR.exists():
        shutil.rmtree(ANSWERS_DIR)
    ANSWERS_DIR.mkdir(parents=True)

    public = [public_entry(e) for e in entries]
    STATIC_MANIFEST.write_text(json.dumps(public, separators=(",", ":")))

    for e in entries:
        h = verifier(e["id"], e["tonic_pc"], e["mode"])
        (ANSWERS_DIR / f"{e['id']}.json").write_text(json.dumps({"h": h}, separators=(",", ":")))

    # The page reads its audio host from here, so the same app.js works both on
    # Pages (HF CDN) and under the demoted fallback server (which overrides this
    # route to point at its own /audio).
    CONFIG_JSON.write_text(json.dumps({
        "audio_base": HF_AUDIO_BASE,
        "api": False,
        "note": "Static build. audio_base must stay the resolve/ URL — what it "
                "redirects to is signed and expires.",
    }, indent=1))

    report = {
        "entries": len(public),
        "answer_files": len(list(ANSWERS_DIR.glob("*.json"))),
        "manifest_bytes": STATIC_MANIFEST.stat().st_size,
        "pool": list(pool),
    }
    print(f"manifest : {report['entries']} entries, "
          f"{report['manifest_bytes'] / 1e6:.2f} MB uncompressed -> {STATIC_MANIFEST}")
    print(f"answers  : {report['answer_files']} verifier files -> {ANSWERS_DIR}")
    print(f"config   : {CONFIG_JSON}")
    return report


if __name__ == "__main__":
    build()
