#!/usr/bin/env python
"""GATE 6 — the front panel, verified on headless WebKit.

Runs the Playwright suite at a desktop viewport and an iPhone viewport. WebKit
is the stand-in for iOS Safari; there is no device farm (SPEC decision 10), so
the hardware silent switch stays a post-build human check and everything a
browser *can* prove is proved here.

A missing browser binary is a FAILURE, not a skip: a gate that passes because
it did not run is worse than no gate.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BROWSERS = ROOT / ".playwright"
FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}{(' — ' + detail) if detail else ''}")
    if not ok:
        FAILURES.append(name)


def strip_js_comments(src: str) -> str:
    """Remove /* */ and // comments so a prose mention isn't read as a call."""
    out, i, n = [], 0, len(src)
    while i < n:
        if src.startswith("/*", i):
            end = src.find("*/", i + 2)
            i = n if end == -1 else end + 2
        elif src.startswith("//", i):
            end = src.find("\n", i)
            i = n if end == -1 else end
        else:
            out.append(src[i])
            i += 1
    return "".join(out)


def static_invariants() -> None:
    """Source-level invariants, checked without a browser."""
    app_js = strip_js_comments((ROOT / "web" / "app.js").read_text())
    index = (ROOT / "web" / "index.html").read_text()

    check("no <audio> element in the markup", "<audio" not in index.lower())
    check("no `new Audio(` in the page script", "new Audio(" not in app_js)
    check("clip loops via AudioBufferSourceNode.loop", "source.loop = state.looping" in app_js)
    check("no localStorage / sessionStorage / cookie writes",
          "localStorage" not in app_js and "sessionStorage" not in app_js
          and "document.cookie" not in app_js)
    check("AudioContext is constructed in exactly one place", app_js.count("new Ctor()") == 1)


def main() -> int:
    print("=== GATE 6 — frontend ===")
    static_invariants()

    manifest = ROOT / "build" / "manifest.json"
    if not manifest.exists():
        check("manifest exists for the e2e server", False, f"{manifest} missing — run phase 4")
        print("\nGATE 6 FAILED: no manifest to serve")
        return 1

    env = dict(os.environ)
    env["npm_config_cache"] = str(ROOT / ".npm-cache")

    # The browser may live in the project-local dir or in Playwright's shared
    # cache, depending on where it was installed from. Prefer whichever has a
    # WebKit build rather than forcing one and reporting a false absence.
    shared = Path.home() / ".cache" / "ms-playwright"
    installed: list[Path] = []
    for candidate in (BROWSERS, shared):
        if candidate.exists():
            found = sorted(candidate.glob("webkit-*"))
            if found:
                installed = found
                env["PLAYWRIGHT_BROWSERS_PATH"] = str(candidate)
                break
    if not installed:
        check("headless WebKit is installed", False,
              "no webkit build under .playwright — `npx playwright install webkit` is "
              "blocked by the sandbox allowlist (cdn.playwright.dev, "
              "playwright.download.prss.microsoft.com return 403)")
        print("\nGATE 6 FAILED: the browser this gate requires is not available.")
        print("This is an environment blocker, not a code failure — do not skip the gate.")
        return 1
    check("headless WebKit is installed", True, installed[0].name)

    proc = subprocess.run(["npx", "playwright", "test"], cwd=ROOT, env=env,
                          capture_output=True, text=True)
    print(proc.stdout[-8000:])
    if proc.returncode != 0:
        print(proc.stderr[-4000:])
    check("playwright suite passes at desktop and iPhone viewports", proc.returncode == 0)

    print()
    if FAILURES:
        print(f"GATE 6 FAILED: {len(FAILURES)} check(s) failed: {', '.join(FAILURES)}")
        return 1
    print("GATE 6 PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
