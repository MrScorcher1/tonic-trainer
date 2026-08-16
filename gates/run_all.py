#!/usr/bin/env python
"""Run the whole pipeline, phase by phase, stopping at the first failed gate.

This is the spec's rule 1 made executable: phase N+1 does not start until phase
N's gate exits zero. Re-running is safe — every phase is idempotent and skips
work it already did.

    python gates/run_all.py            # everything
    python gates/run_all.py --from 4   # resume at phase 4
    python gates/run_all.py --gates    # gates only, no rebuilding
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = str(ROOT / ".venv" / "bin" / "python")

# (phase key, human name, build command or None, gate script)
STAGES = [
    ("0", "scaffold", None, "gates/gate0.py"),
    ("1", "keys", [PY, "-m", "tonic_trainer.phase1"], "gates/gate1.py"),
    ("2", "metadata + license", [PY, "-m", "tonic_trainer.phase2"], "gates/gate2.py"),
    ("3", "audio acquisition", [PY, "-m", "tonic_trainer.phase3"], None),
    ("3b", "clip derivation", [PY, "-m", "tonic_trainer.phase3_clips"], "gates/gate3.py"),
    ("4", "manifest", [PY, "-m", "tonic_trainer.manifest"], "gates/gate4.py"),
    ("4b", "audio/label validation", [PY, "-m", "tonic_trainer.validation"], "gates/gate4b.py"),
    ("5", "server", None, "gates/gate5.py"),
    ("6", "frontend", None, "gates/gate6.py"),
]


def run(cmd: list[str], label: str) -> int:
    print(f"\n{'=' * 70}\n>>> {label}\n{'=' * 70}", flush=True)
    started = time.time()
    rc = subprocess.call(cmd, cwd=ROOT)
    print(f"--- {label}: exit {rc} in {time.time() - started:.1f}s", flush=True)
    return rc


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--from", dest="start", default="0", help="phase key to start at")
    parser.add_argument("--gates", action="store_true", help="run gates only, skip builds")
    args = parser.parse_args()

    keys = [s[0] for s in STAGES]
    if args.start not in keys:
        print(f"unknown phase {args.start!r}; choose from {keys}")
        return 2
    start_at = keys.index(args.start)

    for key, name, build, gate in STAGES[start_at:]:
        if build and not args.gates:
            if run(build, f"phase {key} — {name}") != 0:
                print(f"\nSTOPPED: phase {key} ({name}) failed to build.")
                return 1
        if gate:
            if run([PY, gate], f"gate {key} — {name}") != 0:
                print(f"\nSTOPPED at gate {key} ({name}). Phase {key} is not complete;")
                print("do not weaken the gate to get past it.")
                return 1

    print("\nAll gates passed: 0, 1, 2, 3, 4, 4b, 5, 6.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
