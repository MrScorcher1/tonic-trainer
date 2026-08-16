#!/usr/bin/env python
"""GATE 7 — the publication bundle is honest, and nothing publishes itself.

  * dry-run prints every file with a per-file licence column and writes nothing
  * `--confirm` materialises the bundle, and still does not push
  * puzzles_public.json contains none of the answer fields
  * the dataset card carries per-track attribution and a licence statement
  * no code path in this repo invokes a push
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from tonic_trainer.hf_bundle import ANSWER_FIELDS, BUNDLE  # noqa: E402

PY = str(ROOT / ".venv" / "bin" / "python")
FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}{(' — ' + detail) if detail else ''}")
    if not ok:
        FAILURES.append(name)


def main() -> int:
    print("=== GATE 7 — publication bundle ===")

    dry = subprocess.run([PY, "-m", "tonic_trainer.hf_bundle"], cwd=ROOT,
                         capture_output=True, text=True)
    check("dry run exits zero", dry.returncode == 0, dry.stderr.strip()[-300:])
    check("dry run prints a per-file licence column", "licence" in dry.stdout, "")
    check("dry run says it wrote nothing", "DRY RUN" in dry.stdout)
    check("dry run wrote nothing", not BUNDLE.exists() or not any(BUNDLE.rglob("*.mp3")),
          "bundle already materialised (re-run after removing build/hf_upload to retest)")

    real = subprocess.run([PY, "-m", "tonic_trainer.hf_bundle", "--confirm"], cwd=ROOT,
                          capture_output=True, text=True)
    check("--confirm exits zero", real.returncode == 0, real.stderr.strip()[-300:])
    check("--confirm states that it did not push", "NOT been pushed" in real.stdout)

    for name in ("README.md", "UPLOAD.md", "attribution.csv", "puzzles_public.json"):
        check(f"bundle contains {name}", (BUNDLE / name).exists())

    if (BUNDLE / "puzzles_public.json").exists():
        raw = (BUNDLE / "puzzles_public.json").read_text()
        leaked = [f for f in ANSWER_FIELDS if f in raw]
        check("puzzles_public.json omits every answer field", not leaked, str(leaked))
        entries = json.loads(raw)
        check("public puzzles keep attribution",
              all(e.get("title") and e.get("artist") and e.get("license") for e in entries))

    if (BUNDLE / "README.md").exists():
        card = (BUNDLE / "README.md").read_text()
        check("dataset card states audio keeps its own CC terms",
              "original Creative Commons terms" in card)
        check("dataset card states attribution is mandatory", "Attribution is mandatory" in card)
        check("dataset card excludes NoDerivatives", "NoDerivatives" in card)

    if (BUNDLE / "attribution.csv").exists():
        rows = (BUNDLE / "attribution.csv").read_text().strip().splitlines()
        clips = list((BUNDLE / "clips").rglob("*.mp3")) if (BUNDLE / "clips").exists() else []
        check("attribution.csv has one row per clip", len(rows) - 1 == len(clips),
              f"{len(rows) - 1} rows vs {len(clips)} clips")

    sources = "\n".join(p.read_text() for p in (ROOT / "src").rglob("*.py"))
    pushes = re.findall(r"hf\s+upload|upload_folder|create_commit|HfApi\(", sources)
    check("no code path pushes to the Hub", not pushes, str(set(pushes)))

    print()
    if FAILURES:
        print(f"GATE 7 FAILED: {len(FAILURES)} check(s) failed: {', '.join(FAILURES)}")
        return 1
    print("GATE 7 PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
