#!/usr/bin/env python
"""GATE D1 — computed difficulty is real, reproducible, and honestly described.

  * every entry has an integer difficulty in {1,2,3} and a non-empty genre
  * no tier label survives anywhere; the JSON Schema enum matches and validates
  * each level holds >= 200 entries (terciles make this true by construction, so
    a failure means the binning is broken)
  * the computation is REPRODUCIBLE: re-running it reproduces both the per-clip
    margins and the tercile cut points exactly
  * the degenerate guard fires: a synthetic constant tone is rated 3, not 1 —
    the regression test for the drone failure, without which it silently returns
  * the docs state that difficulty is computed, unvalidated against human
    performance, and relative to this corpus

The browser half (rating control, batching, exit flush, fail-soft, shrinkage,
manual advance, zero storage) lives in tests/e2e/difficulty.spec.js and is run
from here.

ANCHORING GUARD, RETIRED. The spec required difficulty to be hidden until the
player rated. The USER retired that on 2026-08-16 ("I want ppl to be able to see
the difficulty from the beginning tho even before answering idc about anchoring
bias"). The assertion is inverted rather than deleted — see the REMOVAL-OK note
in the e2e spec — and the cost is recorded in the README: ratings are now
anchored, so only DISAGREEMENT with the prior is evidence.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "gates"))

import jsonschema  # noqa: E402

from _playwright import browser_env, run_suite  # noqa: E402

from tonic_trainer.difficulty import (  # noqa: E402
    DIFFICULTY_JSON,
    MIN_PER_LEVEL,
    analyse_clip,
    bin_difficulty,
)
from tonic_trainer.manifest import MANIFEST_JSON, load_manifest  # noqa: E402

SCHEMA = ROOT / "tests" / "manifest.schema.json"
TIER_WORDS = ("tier1", "tier2", "tier3", "untagged")
FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}{(' — ' + detail) if detail else ''}")
    if not ok:
        FAILURES.append(name)


def constant_tone_is_rated_hardest() -> None:
    """The drone regression test.

    A sustained tone earns a huge tonic margin and would be rated EASY on the
    margins alone — while being the worst possible puzzle, because droning a
    reference pitch against a track that is itself a drone is meaningless. If
    this stops firing, that failure returns silently.
    """
    from tonic_trainer import difficulty as diff_mod

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "000").mkdir()
        clip = root / "000" / "000001.mp3"
        proc = subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
             "-i", "sine=frequency=261.63:duration=30", "-ac", "1", "-ar", "44100",
             "-b:a", "128k", str(clip)],
            capture_output=True, text=True,
        )
        if proc.returncode != 0:
            check("synthetic constant-tone fixture builds", False, proc.stderr[-200:])
            return

        original_root = diff_mod.CLIP_ROOT
        diff_mod.CLIP_ROOT = root
        try:
            metrics = diff_mod.analyse_clip(("fma-000001", "000/000001.mp3", 0, "major"))
        finally:
            diff_mod.CLIP_ROOT = original_root

        if not hasattr(metrics, "chroma_variance"):
            check("the constant tone analyses", False, str(metrics))
            return

        # Bin it against the real corpus, so the real cut points apply.
        payload = json.loads(DIFFICULTY_JSON.read_text())
        corpus = [
            diff_mod.ClipMetrics(**m) for m in payload["metrics"].values()
        ]
        ratings, _cuts = diff_mod.bin_difficulty(corpus + [metrics])
        level = ratings[metrics.id]
        check("a synthetic constant tone is rated 3, not 1", level == 3,
              f"rated {level}, chroma_variance {metrics.chroma_variance:.6f}, "
              f"score {metrics.score:.4f}")


def reproducible() -> None:
    """Same corpus, same numbers — including the cut points."""
    payload = json.loads(DIFFICULTY_JSON.read_text())
    from tonic_trainer.difficulty import ClipMetrics

    stored = [ClipMetrics(**m) for m in payload["metrics"].values()]
    ratings, cuts = bin_difficulty(stored)

    same_ratings = ratings == payload["ratings"]
    check("re-binning the stored metrics reproduces every rating", same_ratings,
          "" if same_ratings else
          f"{sum(1 for k, v in ratings.items() if payload['ratings'].get(k) != v)} differ")

    for key in ("easy_cut_score", "hard_cut_score", "low_variance", "high_variance"):
        check(f"cut point {key} is reproduced", cuts[key] == payload["cut_points"][key],
              f"{cuts[key]} vs {payload['cut_points'][key]}")

    # Re-analysing audio must give bit-identical margins, or "reproducible"
    # only means "we saved the answer".
    entries = load_manifest()[:8]
    mismatched = []
    for e in entries:
        fresh = analyse_clip((e["id"], e["audio_path"], e["tonic_pc"], e["mode"]))
        was = payload["metrics"].get(e["id"])
        if not was or abs(fresh.score - was["score"]) > 1e-9:
            mismatched.append(e["id"])
    check("re-analysing audio reproduces the stored margins", not mismatched,
          f"{len(mismatched)}: {mismatched[:3]}")


def main() -> int:
    print("=== GATE D1 — computed difficulty and player ratings ===")

    entries = load_manifest()
    schema = json.loads(SCHEMA.read_text())

    try:
        jsonschema.validate(entries, schema)
        check("manifest validates against the updated schema", True, f"{len(entries)} entries")
    except jsonschema.ValidationError as exc:
        check("manifest validates against the updated schema", False, exc.message)

    bad_level = [e["id"] for e in entries
                 if not isinstance(e.get("difficulty"), int) or e["difficulty"] not in (1, 2, 3)]
    check("every entry has an integer difficulty in {1,2,3}", not bad_level, f"{len(bad_level)}")

    bad_genre = [e["id"] for e in entries if not str(e.get("genre", "")).strip()]
    check("every entry has a non-empty genre", not bad_genre, f"{len(bad_genre)}")

    raw = MANIFEST_JSON.read_text()
    tiers_present = [w for w in TIER_WORDS if f'"{w}"' in raw]
    check("no tier label survives in the manifest", not tiers_present, str(tiers_present))
    schema_enum = schema["items"]["properties"]["difficulty"].get("enum")
    check("the schema enum is the integer levels", schema_enum == [1, 2, 3], str(schema_enum))

    counts = {level: sum(1 for e in entries if e["difficulty"] == level) for level in (1, 2, 3)}
    for level in (1, 2, 3):
        check(f"difficulty {level} holds >= {MIN_PER_LEVEL} entries",
              counts[level] >= MIN_PER_LEVEL, str(counts[level]))

    reproducible()
    constant_tone_is_rated_hardest()

    readme = (ROOT / "README.md").read_text()
    check("the docs say difficulty is computed and unvalidated",
          "computed, not validated" in readme.lower()
          or "It is computed, not validated" in readme)
    check("the docs bound the claim to this corpus", "terciles of this corpus" in readme)
    check("the docs narrow what ratings can prove",
          "only claim they support" in readme and "Disagreement is evidence" in readme)

    ratings_js = (ROOT / "docs" / "ratings.js").read_text()
    check("the ratings module records the anchoring cost",
          "AGREEMENT does not" in ratings_js and "flag individual badly-misrated" in ratings_js.lower()
          or "FLAG INDIVIDUAL BADLY-MISRATED SONGS" in ratings_js)

    _env, build = browser_env()
    check("headless WebKit is installed", build is not None, str(build))
    if build is None:
        print("\nGATE D1 FAILED: the browser the rating assertions need is not available.")
        return 1

    rc, output = run_suite(["tests/e2e/difficulty.spec.js"], quiet_tail=6000)
    print(output)
    check("the rating-loop suite passes at both viewports", rc == 0)

    print()
    print(f"difficulty spread: {counts[1]} / {counts[2]} / {counts[3]}")
    cuts = json.loads(DIFFICULTY_JSON.read_text())["cut_points"]
    print(f"cut points: easy >= {cuts['easy_cut_score']:.4f}, hard < {cuts['hard_cut_score']:.4f}")
    print(f"variance guard moved {len(cuts['forced_to_3'])} clips to level 3")

    print()
    if FAILURES:
        print(f"GATE D1 FAILED: {len(FAILURES)} check(s) failed: {', '.join(FAILURES)}")
        return 1
    print("GATE D1 PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
