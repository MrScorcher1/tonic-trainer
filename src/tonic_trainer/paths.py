"""Canonical project paths. Every module resolves artifacts through here."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
BUILD = ROOT / "build"
WEB = ROOT / "docs"   # the static site is the single copy of the frontend
TESTS = ROOT / "tests"

for _d in (DATA, BUILD):
    _d.mkdir(parents=True, exist_ok=True)
