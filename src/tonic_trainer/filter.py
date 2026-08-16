"""License classification and filtering (SPEC §1.4).

FMA does not own the audio; each track carries its own license, and the field
in `tracks.csv` is free text — 113 distinct strings across the corpus, spelling
the same license as ``NoDerivatives``, ``NoDerivs``, ``No Derivative Works``,
or (FMA's own nickname for BY-NC-ND) ``Music Sharing``.

Two consequences drive the design:

* **ND detection is token-based, never a bare substring match.** A literal
  case-insensitive search for "nd" hits ``Netherlands``, ``England``,
  ``Finland``, ``Switzerland`` and ``Sound Recording`` — 20 innocent license
  strings in this corpus. The marker being guarded is the NoDerivatives clause,
  so that is what is matched. (No FMA license string contains ``ND`` as a
  standalone token; the gate asserts that separately.)
* **Unknown strings are excluded, not assumed.** Only license families we can
  name are allowed through; anything else is dropped with a logged reason.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import pandas as pd

# --- canonical license identifiers -----------------------------------------

CC0 = "CC0-1.0"
PUBLIC_DOMAIN = "Public Domain"
CC_BY = "CC BY"
CC_BY_SA = "CC BY-SA"
CC_BY_NC = "CC BY-NC"
CC_BY_NC_SA = "CC BY-NC-SA"
CC_BY_ND = "CC BY-ND"
CC_BY_NC_ND = "CC BY-NC-ND"
FAL = "Free Art License"
EFF_OAL = "EFF Open Audio License"
SAMPLING_PLUS = "Sampling Plus"
NC_SAMPLING_PLUS = "NonCommercial Sampling Plus"
UNKNOWN = "UNKNOWN"

# NoDerivatives, in every spelling this corpus uses, plus the SPDX-style token.
_ND_PATTERNS = (
    r"noderivat",          # NoDerivatives
    r"noderivs",           # NoDerivs
    r"no\s+derivative",    # No Derivative Works
    r"\bnd\b",             # BY-NC-ND style token
    r"by-nc-nd",
    r"by-nd",
)
_ND_RE = re.compile("|".join(_ND_PATTERNS), re.IGNORECASE)

# FMA labels CC BY-NC-ND 3.0 as "(aka Music Sharing)". A bare "Music Sharing"
# string is therefore that same ND license.
_MUSIC_SHARING_RE = re.compile(r"^\s*music sharing\s*$", re.IGNORECASE)

_NC_RE = re.compile(r"non-?commercial|\bnc\b", re.IGNORECASE)
_SA_RE = re.compile(r"share\s*-?\s*alike|sharealike|\bsa\b", re.IGNORECASE)
_ATTRIBUTION_RE = re.compile(r"attribution|\bby\b", re.IGNORECASE)

# Non-CC families we can name. Anything not matched here and not
# Attribution-shaped is UNKNOWN and gets excluded.
_NAMED_FAMILIES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"^\s*cc0\b", re.IGNORECASE), CC0),
    (re.compile(r"public domain", re.IGNORECASE), PUBLIC_DOMAIN),
    (re.compile(r"art libre|free art license", re.IGNORECASE), FAL),
    (re.compile(r"open audio license", re.IGNORECASE), EFF_OAL),
    (re.compile(r"noncommercial sampling plus", re.IGNORECASE), NC_SAMPLING_PLUS),
    (re.compile(r"^\s*sampling plus", re.IGNORECASE), SAMPLING_PLUS),
)

# Canonical ids that permit derivative works (looping/slicing a clip).
DERIVATIVE_OK = frozenset(
    {CC0, PUBLIC_DOMAIN, CC_BY, CC_BY_SA, CC_BY_NC, CC_BY_NC_SA, FAL, EFF_OAL,
     SAMPLING_PLUS, NC_SAMPLING_PLUS}
)
NONCOMMERCIAL = frozenset({CC_BY_NC, CC_BY_NC_SA, CC_BY_NC_ND, NC_SAMPLING_PLUS})


@dataclass(frozen=True)
class LicenseVerdict:
    canonical: str
    is_nd: bool
    is_nc: bool
    allowed: bool
    reason: str


def classify_license(raw: object) -> LicenseVerdict:
    """Classify one free-text license string. Never guesses: unknown -> excluded."""
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return LicenseVerdict(UNKNOWN, False, False, False, "license field is empty")
    text = str(raw).strip()
    if not text:
        return LicenseVerdict(UNKNOWN, False, False, False, "license field is empty")

    is_nd = bool(_ND_RE.search(text)) or bool(_MUSIC_SHARING_RE.match(text))
    is_nc = bool(_NC_RE.search(text))

    canonical = UNKNOWN
    for pattern, name in _NAMED_FAMILIES:
        if pattern.search(text):
            canonical = name
            break

    if canonical == UNKNOWN:
        if _MUSIC_SHARING_RE.match(text):
            canonical = CC_BY_NC_ND
        elif _ATTRIBUTION_RE.search(text):
            if is_nc and is_nd:
                canonical = CC_BY_NC_ND
            elif is_nd:
                canonical = CC_BY_ND
            elif is_nc and _SA_RE.search(text):
                canonical = CC_BY_NC_SA
            elif is_nc:
                canonical = CC_BY_NC
            elif _SA_RE.search(text):
                canonical = CC_BY_SA
            else:
                canonical = CC_BY

    if canonical == UNKNOWN:
        return LicenseVerdict(UNKNOWN, is_nd, is_nc, False, f"unrecognized license family: {text!r}")
    if is_nd or canonical not in DERIVATIVE_OK:
        return LicenseVerdict(canonical, True, is_nc, False, "NoDerivatives — looping a clip is a derivative work")
    return LicenseVerdict(canonical, False, is_nc, True, "ok")


def has_nd_marker(text: object) -> bool:
    """True if the string carries a NoDerivatives marker in any spelling."""
    return classify_license(text).is_nd


def apply_license_filter(df: pd.DataFrame, license_col: str = "license") -> pd.DataFrame:
    """Add classification columns. Does not drop rows — the caller decides."""
    verdicts = [classify_license(v) for v in df[license_col]]
    out = df.copy()
    out["license_canonical"] = [v.canonical for v in verdicts]
    out["license_is_nd"] = [v.is_nd for v in verdicts]
    out["license_is_nc"] = [v.is_nc for v in verdicts]
    out["license_allowed"] = [v.allowed for v in verdicts]
    out["license_reason"] = [v.reason for v in verdicts]
    return out


def published_fma_sha1(filename: str, readme_text: str) -> str:
    """Read the published SHA1 for an FMA archive out of mdeff/fma's README.

    Read rather than hardcoded: a checksum recalled from memory verifies
    nothing (SPEC §1.2 — "verify against the published SHA1").
    """
    pattern = re.compile(rf"([0-9a-f]{{40}})\s+`?{re.escape(filename)}`?", re.IGNORECASE)
    match = pattern.search(readme_text)
    if match:
        return match.group(1).lower()
    alt = re.compile(rf"`?{re.escape(filename)}`?[^\n]*?sha1[^0-9a-f]*([0-9a-f]{{40}})", re.IGNORECASE)
    match = alt.search(readme_text)
    if not match:
        raise ValueError(f"no published SHA1 for {filename} found in the FMA README")
    return match.group(1).lower()
