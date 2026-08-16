#!/usr/bin/env python
"""GATE 5 — the server is correct and stateless.

Runs the server test suite, then repeats the load-bearing assertions against the
*real* manifest and real clips, because a fixture can only prove the code path,
not the corpus it will actually serve.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}{(' — ' + detail) if detail else ''}")
    if not ok:
        FAILURES.append(name)


def real_manifest_checks() -> None:
    from fastapi.testclient import TestClient

    from tonic_trainer import server
    from tonic_trainer.manifest import MANIFEST_JSON, load_manifest

    if not MANIFEST_JSON.exists():
        check("real manifest exists", False, f"{MANIFEST_JSON} missing")
        return

    entries = load_manifest()
    client = TestClient(server.create_app(entries))

    raw = client.get("/api/puzzle").text
    leaked = [w for w in ("tonic_pc", "mode", "key_display") if w in raw]
    check("real /api/puzzle leaks no answer field", not leaked, f"leaked {leaked}")

    ids = {client.get("/api/puzzle").json()["id"] for _ in range(50)}
    check("50 real /api/puzzle calls return >= 40 distinct ids", len(ids) >= 40, str(len(ids)))

    puzzle = client.get("/api/puzzle").json()
    entry = next(e for e in entries if e["id"] == puzzle["id"])
    good = client.post("/api/guess", json={
        "id": entry["id"], "tonic_pc": entry["tonic_pc"], "mode": entry["mode"]}).json()
    check("correct answer for a real puzzle scores correct", good["correct"] is True, str(good["relative_error"]))

    rel_pc = (entry["tonic_pc"] - 3) % 12 if entry["mode"] == "major" else (entry["tonic_pc"] + 3) % 12
    rel_mode = "minor" if entry["mode"] == "major" else "major"
    rel = client.post("/api/guess", json={
        "id": entry["id"], "tonic_pc": rel_pc, "mode": rel_mode}).json()
    check("its relative major/minor scores relative",
          rel["correct"] is False and rel["relative_error"] == "relative", str(rel["relative_error"]))

    full = client.get(puzzle["audio_url"])
    size = len(full.content)
    part = client.get(puzzle["audio_url"], headers={"Range": "bytes=0-2047"})
    check("real audio honours Range with 206 + Content-Range",
          part.status_code == 206 and part.headers.get("content-range") == f"bytes 0-2047/{size}",
          f"{part.status_code} {part.headers.get('content-range')}")

    body = {"id": entry["id"], "tonic_pc": 0, "mode": "major"}
    first = client.post("/api/guess", json=body).content
    same = all(client.post("/api/guess", json=body).content == first for _ in range(20))
    check("identical guesses return byte-identical responses", same)
    check("no cookies are set", not client.cookies, str(dict(client.cookies)))


def main() -> int:
    print("=== GATE 5 — server ===")
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "--no-header", "tests/test_server.py"],
        cwd=ROOT, capture_output=True, text=True,
    )
    tail = [ln for ln in (proc.stdout + proc.stderr).strip().splitlines() if ln][-1]
    check("server test suite passes", proc.returncode == 0, tail)
    if proc.returncode != 0:
        print(proc.stdout[-4000:])

    print()
    print("--- against the real manifest ---")
    real_manifest_checks()

    print()
    if FAILURES:
        print(f"GATE 5 FAILED: {len(FAILURES)} check(s) failed: {', '.join(FAILURES)}")
        return 1
    print("GATE 5 PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
