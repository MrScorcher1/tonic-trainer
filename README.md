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

**Gate 4b is the one that catches the failure nothing else would**: correct
audio, correct labels, joined on the wrong key. It passes only if agreement is
solidly above chance *and* below suspicion, with `relative` as the largest error
bucket — the signature of a real human-labelled set analysed by an imperfect
algorithm. A shuffled-label control must collapse to chance, or the metric
itself is meaningless.

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
* **NonCommercial is allowed and flagged** — it only bites on monetization.
* **Attribution is mandatory.** A track without a title and artist never becomes
  a puzzle, and the attribution line is always on screen.

### One correction to the original spec

The spec assumed the Zenodo FMAK package shipped 30-second clips "cut from the
middle of each track". It does not — it ships **full-length** audio (median
7.6 MB, ~210 s). So the 30-second loop is derived here instead: `clips.py` cuts
it from the middle of each track with ffmpeg and re-encodes to mono 128 kbps,
which keeps the documented "no song openings" limitation intact, keeps the
25–35 s gate honest, and drops the per-puzzle transfer from ~7 MB to ~0.5 MB.

## Known limitations

* Clips come from track middles, so you never hear a song opening — where tonics
  are most clearly established.
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
