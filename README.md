# Tonic Trainer

A local-first, stateless ear-training unit. It loops a clip of real music; you
hunt the tonic by droning a reference pitch against it, pick major or minor, and
the app scores you — including *how* you missed, which is the part that teaches.

Built on the Free Music Archive's Creative Commons audio with the human-made key
annotations from [`fma_keys`/FMAK](https://github.com/stellaywong/fma_keys).

## Quickstart

```bash
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -e ".[dev]"

python gates/run_all.py          # builds every phase, stops at the first failed gate
.venv/bin/python -m tonic_trainer.server        # http://127.0.0.1:8000
```

The full build downloads ~39 GB of audio from Zenodo and derives ~3,600 clips.
Expect a couple of hours on a normal connection; every step is resumable and
idempotent, so re-running picks up where it stopped.

### Where the audio comes from

By default the server reads clips from `build/clips/`. Two other modes exist for
hosting the audio elsewhere (e.g. a Hugging Face dataset):

```bash
TT_AUDIO_BASE="https://.../clips" python -m tonic_trainer.server   # browser fetches directly
python -m tonic_trainer.server --audio-base "https://.../clips" --audio-proxy
```

Direct fetch works with the published dataset: measured 2026-08-16, the
`resolve/` URL 302s to the CDN and *that* response — the one carrying the bytes —
sends `Access-Control-Allow-Origin: *`, which is origin-independent and so works
from a LAN address as well as localhost. The server re-checks this at startup and
prints a warning naming `--audio-proxy` if the header ever disappears, so a
change at the host's end shows up as a boot message rather than silent playback
failure.

`--audio-proxy` remains the robust fallback: it fetches each clip server-side,
caches it, and serves it same-origin with Range/206 intact, so the host's CORS
policy stops mattering.

**Never cache the resolved CDN URL.** `resolve/` redirects to a *signed* URL with
an `Expires` timestamp and a Policy/Signature pair. Always request the `resolve/`
URL and let it redirect fresh; storing the resolved one works until the signature
expires and then breaks playback for everyone.

### Serving to a phone

```bash
.venv/bin/python -m tonic_trainer.server --lan     # QR code, same network only
```

`--lan` binds `0.0.0.0` and prints a QR code whose URL carries a random path
prefix; requests without that prefix are 404. Nothing leaves your network.

`--tunnel` (public HTTPS via cloudflared) exists but **refuses to run without
`--confirm-public`**. Putting CC-licensed audio on a public URL is a legal
judgment call, and it belongs to you, not to the program.

## How it plays

Tapping a piano key does two things at once: it drones that pitch *over* the
looping clip, and it arms that pitch as your answer. Hunt until one note stops
fighting the music. MAJOR/MINOR is a separate switch. CHECK commits.

The keyboard shows three distinct states — **idle**, **auditioning** (sounding
right now), **committed** (armed for CHECK) — so exploring never turns into an
accidental submission.

The result panel names the *kind* of miss:

| verdict | what it means |
|---|---|
| `relative` | right notes, wrong home — you heard C major where the answer was A minor. The interesting error. |
| `parallel` | right tonic, wrong colour (C major vs C minor). |
| `semitone` | your drone was a hair sharp or flat. |
| `fifth` | you locked onto the dominant instead of the tonic. |

## Stateless by construction

No accounts, no database, no sessions. `/api/guess` is a pure function of the
manifest: the same request returns byte-identical bytes and records nothing.
Session stats live in JavaScript memory and die on reload — every visit is new.
The page writes no `localStorage`, no `sessionStorage`, no cookies, and the
server sets none.

The only server-side writes are operator artifacts under `build/` (notably
`disputed.jsonl`, which describes the corpus, not any user).

## Build phases and gates

Every phase ends in a gate that exits non-zero on failure, and phase N+1 does
not start until phase N's gate passes. `gates/run_all.py` enforces the order.

| gate | proves |
|---|---|
| 0 | scaffold: package and every dependency import, pytest collects clean |
| 1 | 5,489 key rows normalize to 24 (tonic, mode) pairs and round-trip 100% against the source's own inconsistent spelling |
| 2 | the FMA metadata join yields ≥1,000 licensed, attributable tracks |
| 3 | the audio really downloaded (per-file checksums, ≥3,000 tracks) and the served clips are 25–35 s of readable audio |
| 4 | the manifest validates against a JSON Schema, every clip resolves, every entry is attributable, no ND licenses, tier1 ≥200, no key over 25% of the pool |
| 4b | the labels actually describe the audio — Krumhansl-Schmuckler agreement with a shuffled-label negative control |
| 5 | the server never leaks an answer, honours Range requests, and holds no per-user state |
| 6 | the front panel's audio invariants and layout hold on headless WebKit at desktop and iPhone viewports |
| 7 | the publication bundle carries per-file licences and attribution, strips the answers, and no code path can push |

**Gate 4b is the one that catches the failure nothing else would**: correct
audio, correct labels, joined on the wrong key. It passes only if agreement is
solidly above chance *and* below suspicion, with a near-key confusion
(`relative` or `fifth`) as the largest error bucket — the signature of a real
human-labelled set analysed by an imperfect algorithm. A shuffled-label control
must collapse to chance, or the metric itself is meaningless.

## Data and licensing

* **Keys** — `fma_keys` (ISMIR 2023 late-breaking demo, Stella Wong). 5,489
  human-annotated tracks. The CSV has two header rows and mixes enharmonic
  spellings (`D# Major` and `Bb Major` in the same file); both are handled.
* **Audio** — [Zenodo record 10719860](https://zenodo.org/records/10719860)
  (FMAK, CC BY 4.0), 39.3 GB in ten range-zips, verified against the MD5s the
  record publishes.
* **Metadata** — `fma_metadata.zip`, verified against the SHA1 published in
  mdeff/fma's README (read from the README at build time, not hardcoded).

Each track carries its own Creative Commons license, and that license governs
the clip regardless of how the compilation is licensed:

* **NoDerivatives tracks are excluded** — slicing a loop out of one is plausibly
  a derivative work. 1,841 of the 5,489 annotated tracks go this way. Detection
  is token-based, because a bare "ND" substring match would also exclude
  *Netherlands*, *England*, *Finland*, *Switzerland* and *Sound Recording*.
* **Unrecognized or missing license fields are excluded**, never assumed.
* **The mp3's own ID3 tags get a vote.** 276 tracks carry a `WCOP`/`TCOP` tag
  claiming BY-NC-ND while `tracks.csv` says otherwise. Those are dropped from the
  served corpus, not merely from publication — slicing a loop from a file that
  claims NoDerivatives is the derivative work the licence forbids, whoever is
  serving it. The other 227 conflicts are NC-vs-not disagreements, which only
  bite on redistribution, so they play locally and stay out of any upload.
* **NonCommercial is allowed and flagged** — it only bites on monetization.
* **Attribution is mandatory.** A track without a title and artist never becomes
  a puzzle, and the attribution line is always on screen.

### Corrections to the original spec

**The FMAK package is not 30-second clips.** The spec assumed it shipped clips
"cut from the middle of each track". It ships **full-length** audio (median
7.6 MB, ~210 s). The 30-second loop is derived here instead: `clips.py` cuts it
with ffmpeg and re-encodes to mono 128 kbps, which keeps the 25–35 s gate honest
and drops the per-puzzle transfer from ~7 MB to ~0.5 MB.

**Clips are openings, not middles.** With full tracks in hand, the spec's "you
never hear a song opening" entry stopped being an inherent limitation and became
a choice — and openings are where a tonic is established, which is the skill
being trained. Measured on the same 300 tracks, openings also beat middles for
tonal clarity: 49.3% vs 44.3% estimator agreement. The cost is that leading
silence becomes possible, so every clip is checked with `volumedetect` and
anything under −55 dBFS is dropped (one clip in 3,610 was digital silence).

**Krumhansl-Schmuckler's characteristic failure is the fifth, not the relative.**
Gate 4b originally required `relative` to be the largest error bucket. It never
is: `fifth` dominated in all five variants tested (both clip positions, three
chroma front-ends), and MIREX's own scoring rates a fifth error as the *closest*
near-miss (correct 1.0, fifth 0.5, relative 0.3, parallel 0.2). The criterion was
amended to accept either, with the evidence recorded in `gates/gate4b.py`. No
numeric threshold was changed.

## Known limitations

* Clips are the opening 30 seconds, so you hear whatever the track starts with —
  sometimes an intro rather than the material that establishes the key.
* `fma_keys` is a lightly-reviewed, single-annotator set. Labels are human-made
  but not cross-checked, hence the FLAG button: a dispute is auto-triaged by
  re-running the estimator on that one track and escalated only when the
  estimator both supports you and opposes the stored label.
* FMA skews independent/electronic/experimental. Tier 1 filtering mitigates
  tonal ambiguity but does not eliminate it.
* The source key distribution is lopsided toward C major and A minor — good for
  practising the relative-pair confusion, bad for remote keys.

## Steps that need a human

* **Publishing consent** — `--tunnel` and any Hugging Face publication (Phase 7)
  both default to no and require an explicit flag.
* **Silent-switch and visual review** — headless WebKit covers the code
  invariants, gesture requirements, looping and layout. What it cannot cover is
  the iPhone hardware mute switch (the single-audio-path design exists precisely
  to survive it) and subjective polish. Open it on a real phone, confirm clip and
  drone are audible together with the switch in both positions.
