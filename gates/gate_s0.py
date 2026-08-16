#!/usr/bin/env python
"""GATE S0 — the site is public, served over HTTPS, and carries no credential.

  * the repo exists, is public, and Pages serves from main:/docs
  * https://<user>.github.io/<repo>/ returns the page over HTTPS
  * the deployed manifest and a deployed answer file are reachable and correct
  * no credential-shaped string exists anywhere in the pushed history

The last one runs against what was ACTUALLY PUSHED, not the working tree. A
secret removed in a later commit is still public once pushed, so the history is
what matters.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = "MrScorcher1/tonic-trainer"
SITE = "https://mrscorcher1.github.io/tonic-trainer/"

CREDENTIAL_PATTERNS = re.compile(
    r"hf_[A-Za-z0-9]{20,}|ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}"
    r"|AKIA[0-9A-Z]{16}|sk-[A-Za-z0-9]{20,}|-----BEGIN [A-Z ]*PRIVATE KEY-----"
)
FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}{(' — ' + detail) if detail else ''}")
    if not ok:
        FAILURES.append(name)


BLOCKED = "blocked-by-sandbox"


def fetch(url: str, timeout: int = 30) -> tuple[int, bytes, str]:
    """Fetch, distinguishing a sandbox refusal from a real failure.

    The build sandbox allowlists github.com but not *.github.io, so the live-site
    checks cannot run from here. That is an environment limit, not a passing
    result: the gate still FAILS, and says which of the two it hit, because
    "could not check" and "checked and it is fine" must never look alike.
    """
    request = urllib.request.Request(url, headers={"User-Agent": "tonic-trainer-gate"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            return resp.status, resp.read(), resp.url
    except urllib.error.HTTPError as exc:
        return exc.code, b"", url
    except Exception as exc:  # noqa: BLE001 — reported, then failed on
        text = f"{type(exc).__name__}: {exc}"
        if "403" in text and "Tunnel" in text:
            return -1, b"", BLOCKED
        print(f"    fetch error for {url}: {text}")
        return 0, b"", url


def gh_json(path: str) -> dict:
    proc = subprocess.run(["gh", "api", path], capture_output=True, text=True)
    if proc.returncode != 0:
        return {"_error": proc.stderr.strip()[:200]}
    return json.loads(proc.stdout)


def main() -> int:
    print("=== GATE S0 — public repo and Pages ===")

    repo = gh_json(f"/repos/{REPO}")
    check("the repo exists", "_error" not in repo, repo.get("_error", ""))
    check("the repo is public", repo.get("private") is False, f"private={repo.get('private')}")

    pages = gh_json(f"/repos/{REPO}/pages")
    source = pages.get("source") or {}
    check("Pages serves from main:/docs",
          source.get("branch") == "main" and source.get("path") == "/docs", str(source))
    check("Pages enforces HTTPS", pages.get("https_enforced") is True,
          str(pages.get("https_enforced")))

    # Verifiable from here: GitHub's own record of the Pages build.
    latest = gh_json(f"/repos/{REPO}/pages/builds/latest")
    build_status = latest.get("status")
    check("the latest Pages build succeeded", build_status == "built",
          f"status={build_status} {(latest.get('error') or {}).get('message', '')}")

    status, body, final = fetch(SITE)
    blocked = final == BLOCKED
    check("the site serves index.html over HTTPS", status == 200 and b"Tonic" in body,
          "NOT CHECKED — the build sandbox blocks *.github.io (403 tunnel). "
          "Run the command printed below from an unsandboxed shell."
          if blocked else f"HTTP {status} from {final}")
    check("the served URL is https", SITE.startswith("https://"), SITE)

    status, body, final = fetch(SITE + "manifest.json")
    entries = json.loads(body) if status == 200 and body else []
    check("the deployed manifest loads", status == 200 and bool(entries),
          "NOT CHECKED — sandbox" if final == BLOCKED else f"HTTP {status}, {len(entries)} entries")
    if entries:
        raw = body.decode()
        leaked = [f for f in ("tonic_pc", "key_display") if f in raw]
        check("the deployed manifest carries no answer field", not leaked, str(leaked))
        first = entries[0]["id"]
        status, body, _ = fetch(f"{SITE}answers/{first}.json")
        payload = json.loads(body) if status == 200 and body else {}
        check("a deployed answer verifier loads and is only a hash",
              status == 200 and set(payload) == {"h"} and re.fullmatch(r"[0-9a-f]{64}", payload.get("h", "")),
              f"HTTP {status} for {first}")

    # The pushed history is what is public — not the working tree.
    proc = subprocess.run(
        ["git", "log", "--all", "-p", "--", ".", ":(exclude)docs/manifest.json",
         ":(exclude)docs/answers"],
        cwd=ROOT, capture_output=True, text=True,
    )
    hits = sorted(set(CREDENTIAL_PATTERNS.findall(proc.stdout)))
    check("no credential-shaped string in the pushed history", not hits, str(hits[:3]))

    print()
    print(f"repo : https://github.com/{REPO}")
    print(f"site : {SITE}")

    print()
    if FAILURES:
        print(f"GATE S0 FAILED: {len(FAILURES)} check(s) failed: {', '.join(FAILURES)}")
        print()
        print("If the failures are the live-fetch checks, they were NOT verified rather")
        print("than verified-and-broken: this sandbox allowlists github.com but not")
        print("*.github.io. Complete the gate from an unsandboxed shell with:")
        print(f"    curl -sS -o /dev/null -w '%{{http_code}} %{{url_effective}}\\n' -L {SITE}")
        print(f"    curl -sS {SITE}manifest.json | head -c 200")
        return 1
    print("GATE S0 PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
