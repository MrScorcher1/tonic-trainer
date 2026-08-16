#!/usr/bin/env python
"""GATE 0 — scaffold is real: package imports, deps import, pytest collects clean.

Exits non-zero on any failure. Never weaken a check to make it pass.
"""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}{(' — ' + detail) if detail else ''}")
    if not ok:
        FAILURES.append(name)


def main() -> int:
    print("=== GATE 0 — scaffold ===")

    try:
        import tonic_trainer

        check("import tonic_trainer", True, f"version {tonic_trainer.__version__}")
    except Exception as exc:  # noqa: BLE001 — a gate reports the failure, then exits non-zero
        check("import tonic_trainer", False, repr(exc))

    for dep in ("pandas", "requests", "fastapi", "uvicorn", "mutagen", "librosa", "qrcode", "jsonschema"):
        try:
            __import__(dep)
            check(f"import {dep}", True)
        except Exception as exc:  # noqa: BLE001
            check(f"import {dep}", False, repr(exc))

    for d in ("data", "build", "src/tonic_trainer", "web", "tests", "gates"):
        check(f"layout {d}/", (ROOT / d).is_dir())

    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "--no-header"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    tail = (proc.stdout + proc.stderr).strip().splitlines()[-3:]
    # rc 5 == "no tests collected", which is a clean empty suite at Phase 0.
    check("pytest collects with zero failures", proc.returncode in (0, 5), " | ".join(tail))

    print()
    if FAILURES:
        print(f"GATE 0 FAILED: {len(FAILURES)} check(s) failed: {', '.join(FAILURES)}")
        return 1
    print("GATE 0 PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
