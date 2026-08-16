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
import os
import re
import subprocess
import sys
import tempfile
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

    # The dry run is exercised against a throwaway directory, so "wrote nothing"
    # is a real assertion rather than a statement about whether the bundle
    # happened to exist already.
    with tempfile.TemporaryDirectory() as tmp:
        probe = Path(tmp) / "bundle"
        env = {**os.environ, "TT_BUNDLE_DIR": str(probe)}
        dry = subprocess.run([PY, "-m", "tonic_trainer.hf_bundle"], cwd=ROOT,
                             capture_output=True, text=True, env=env)
        check("dry run exits zero", dry.returncode == 0, dry.stderr.strip()[-300:])
        check("dry run prints a per-file licence column", "licence" in dry.stdout)
        check("dry run says it wrote nothing", "DRY RUN" in dry.stdout)
        check("dry run really wrote nothing", not probe.exists(),
              f"{probe} was created by a dry run")

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

    # A real check, not a string search: the previous version matched the
    # `hf upload` command printed inside UPLOAD.md's text and called the
    # documentation a push. What actually constitutes a push is importing the
    # Hub client or shelling out to the CLI.
    sources = "\n".join(p.read_text() for p in (ROOT / "src").rglob("*.py"))
    imports = re.findall(r"^\s*(?:from|import)\s+huggingface_hub", sources, re.MULTILINE)
    check("no source module imports the Hub client", not imports, str(imports))

    shells = re.findall(r"""(?:subprocess|Popen|run|call)\s*\(\s*\[?\s*["']hf["']""", sources)
    check("no source module shells out to the hf CLI", not shells, str(shells))

    api_calls = re.findall(r"\b(?:upload_folder|upload_file|create_commit|HfApi)\s*\(", sources)
    check("no source module calls a Hub upload API", not api_calls, str(set(api_calls)))

    print()
    if FAILURES:
        print(f"GATE 7 FAILED: {len(FAILURES)} check(s) failed: {', '.join(FAILURES)}")
        return 1
    print("GATE 7 PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
