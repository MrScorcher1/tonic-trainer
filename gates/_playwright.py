"""Shared Playwright runner for the browser gates.

One place decides where the browser lives and how the suite is invoked, so
Gate 6 and the S-gates cannot drift into disagreeing about either. A missing
browser is a FAILURE everywhere, never a skip: a gate that passes because it did
not run is worse than no gate.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROJECT_LOCAL = ROOT / ".playwright"
SHARED_CACHE = Path.home() / ".cache" / "ms-playwright"


def browser_env() -> tuple[dict[str, str], str | None]:
    """Return (env, webkit build name). The name is None when none is installed."""
    env = dict(os.environ)
    env["npm_config_cache"] = str(ROOT / ".npm-cache")
    for candidate in (PROJECT_LOCAL, SHARED_CACHE):
        if candidate.exists():
            found = sorted(candidate.glob("webkit-*"))
            if found:
                env["PLAYWRIGHT_BROWSERS_PATH"] = str(candidate)
                return env, found[0].name
    return env, None


def run_suite(args: list[str], *, quiet_tail: int = 4000) -> tuple[int, str]:
    env, build = browser_env()
    if build is None:
        return 1, (
            "no webkit build under .playwright or ~/.cache/ms-playwright. "
            "`npx playwright install webkit` is blocked by the sandbox allowlist "
            "(cdn.playwright.dev, playwright.download.prss.microsoft.com return 403), "
            "and its system libraries need `sudo npx playwright install-deps webkit`."
        )
    proc = subprocess.run(["npx", "playwright", "test", *args], cwd=ROOT, env=env,
                          capture_output=True, text=True)
    output = (proc.stdout + proc.stderr)[-quiet_tail:]
    return proc.returncode, output
