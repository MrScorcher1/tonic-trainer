"""Phase 5 — the stateless FastAPI server.

Statelessness is structural, not incidental (SPEC §0.5): there is no session
store, no database, no per-user record anywhere. `/api/guess` is a pure function
of the manifest — the same body always produces byte-identical output, and the
process holds nothing that grows with traffic. The only server-side writes are
operator artifacts (`build/disputed.jsonl`), which describe the corpus, not a
user.

Exposure is opt-in and layered:
  * default            — bind 127.0.0.1, no token, nothing leaves the machine.
  * ``--lan``          — bind 0.0.0.0, random path prefix + token, QR code.
  * ``--tunnel``       — public HTTPS via cloudflared, and refuses to run
                         without ``--confirm-public``, because putting CC audio
                         on a public URL is the user's legal call, not ours.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import re
import secrets
import socket
import subprocess
import sys
import time
from pathlib import Path

from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from .clips import CLIP_ROOT
from .manifest import DEFAULT_POOL, TAGGED_POOL, load_manifest
from .paths import BUILD, WEB
from .scoring import classify, explain

DISPUTED_LOG = BUILD / "disputed.jsonl"
AUDIO_CACHE = BUILD / "audio_cache"
CHUNK = 1 << 18
RANGE_RE = re.compile(r"bytes=(\d*)-(\d*)")
UPSTREAM_TIMEOUT = 30


def fetch_upstream(rel_path: str, audio_base: str) -> Path:
    """Pull one clip from the remote host into the local cache, or raise.

    This is the proxy path (``TT_AUDIO_PROXY=1``). The browser never talks to
    the remote host, so its CORS policy is irrelevant — which matters, because
    Hugging Face's `resolve/` responses are not reliably CORS-enabled and the
    redirect target has been reported failing preflight. Serving same-origin
    turns that from a blocker into a latency question, and keeps Range/206
    working for iOS because the file is on local disk by the time it is served.

    The cache is bounded by the corpus (~0.5 MB x ~3.6k clips), and it is an
    operator artifact, not user state.
    """
    import requests

    target = (AUDIO_CACHE / rel_path).resolve()
    try:
        target.relative_to(AUDIO_CACHE.resolve())
    except ValueError:
        raise HTTPException(status_code=404, detail="not found") from None
    if target.is_file() and target.stat().st_size > 0:
        return target

    url = f"{audio_base}/{rel_path}"
    resp = requests.get(url, timeout=UPSTREAM_TIMEOUT)
    if resp.status_code != 200:
        raise HTTPException(status_code=502,
                            detail=f"upstream {url} returned {resp.status_code}")
    if not resp.content:
        raise HTTPException(status_code=502, detail=f"upstream {url} returned an empty body")
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".part")
    tmp.write_bytes(resp.content)
    tmp.replace(target)
    return target


def check_remote_audio(audio_base: str, sample_path: str, *, timeout: int = 20) -> dict:
    """Ask the remote host, at boot, whether a browser will be allowed to read it.

    Direct fetch depends on the response that carries the BYTES sending
    `Access-Control-Allow-Origin` — not the 302 that points at it. So this
    follows the redirect chain and reports the header on the final hop.

    Measured 2026-08-16 on Hugging Face: hop 1 (huggingface.co) reflects the
    request origin, hop 2 (the CDN, carrying the bytes) sends `*`, which is
    origin-independent and therefore works from LAN addresses too. That can
    change without notice, hence this check: a silent runtime breakage becomes a
    startup message.
    """
    import requests

    url = f"{audio_base}/{sample_path}"
    result: dict = {"url": url, "ok": False, "acao": None, "status": None, "error": None}
    try:
        resp = requests.get(url, headers={"Origin": "http://localhost"}, stream=True,
                            allow_redirects=True, timeout=timeout)
        resp.close()
    except requests.RequestException as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
        return result

    result["status"] = resp.status_code
    result["acao"] = resp.headers.get("access-control-allow-origin")
    result["final_url"] = resp.url
    result["ok"] = resp.status_code == 200 and bool(result["acao"])
    return result


class Guess(BaseModel):
    id: str = Field(min_length=1)
    tonic_pc: int = Field(ge=0, le=11)
    mode: str = Field(pattern="^(major|minor)$")


def _puzzle_payload(entry: dict, prefix: str, audio_base: str = "") -> dict:
    """The public view of a puzzle: attribution and audio, never the answer.

    Field names are listed explicitly rather than filtered out of the entry —
    a new answer-bearing column added upstream must not silently start shipping.

    `audio_base` (from ``TT_AUDIO_BASE``) points the clip at a remote host such
    as a Hugging Face dataset. Unset — the default — serves the clip from this
    process, which always works; turning the remote on or off is one env var,
    not a rebuild.

    **Always emit the `resolve/` URL, never the URL it redirects to.** Hugging
    Face answers `resolve/` with a 302 to a CDN URL that is *signed* — it carries
    an `Expires` timestamp plus a Policy/Signature pair. Caching or persisting
    that resolved URL anywhere (here, in the manifest, in the page, in a
    "helpful" pre-resolution step) works perfectly until the signature expires
    and then breaks playback for everyone. The redirect must be followed fresh
    on every request. This is precisely the kind of thing a later optimisation
    would break, so it is written down at the point where the URL is built.
    """
    return {
        "id": entry["id"],
        "audio_url": (
            f"{audio_base}/{entry['audio_path']}" if audio_base
            else f"{prefix}/audio/{entry['audio_path']}"
        ),
        "title": entry["title"],
        "artist": entry["artist"],
        "license": entry["license"],
        "genre": entry["genre_top"],
        "difficulty": entry["difficulty"],
    }


def _serve_range(path: Path, range_header: str | None) -> Response:
    """Serve a file, honouring a single Range header.

    iOS Safari seeks and re-requests audio; without 206 support the clip either
    fails to play or cannot be looped.
    """
    size = path.stat().st_size
    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    if not range_header:
        return FileResponse(path, media_type=media_type,
                            headers={"Accept-Ranges": "bytes", "Cache-Control": "public, max-age=3600"})

    match = RANGE_RE.fullmatch(range_header.strip())
    if not match:
        raise HTTPException(status_code=416, detail="malformed Range header")
    start_s, end_s = match.groups()
    if start_s == "" and end_s == "":
        raise HTTPException(status_code=416, detail="malformed Range header")
    if start_s == "":  # suffix range: last N bytes
        length = int(end_s)
        if length == 0:
            raise HTTPException(status_code=416, detail="zero-length suffix range")
        start = max(0, size - length)
        end = size - 1
    else:
        start = int(start_s)
        end = int(end_s) if end_s else size - 1
    if start >= size or end < start:
        return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})
    end = min(end, size - 1)

    def stream():
        remaining = end - start + 1
        with path.open("rb") as fh:
            fh.seek(start)
            while remaining > 0:
                block = fh.read(min(CHUNK, remaining))
                if not block:
                    break
                remaining -= len(block)
                yield block

    from starlette.responses import StreamingResponse

    return StreamingResponse(
        stream(),
        status_code=206,
        media_type=media_type,
        headers={
            "Content-Range": f"bytes {start}-{end}/{size}",
            "Content-Length": str(end - start + 1),
            "Accept-Ranges": "bytes",
            "Cache-Control": "public, max-age=3600",
        },
    )


def triage_dispute(entry: dict, guess_pc: int, guess_mode: str) -> dict:
    """Auto-triage a disputed label (SPEC §3).

    Krumhansl-Schmuckler is a *prior* on a dispute a human already raised, never
    an adjudicator on its own. Escalate only when the estimator both supports the
    user and opposes the stored label; log everything else silently.
    """
    from .validation import chroma_vector, estimate_key

    pc, mode = estimate_key(chroma_vector(str(CLIP_ROOT / entry["audio_path"])))
    supports_user = (pc == guess_pc and mode == guess_mode)
    opposes_label = not (pc == entry["tonic_pc"] and mode == entry["mode"])
    return {
        "estimated_tonic_pc": pc,
        "estimated_mode": mode,
        "estimator_supports_user": supports_user,
        "estimator_opposes_label": opposes_label,
        "escalate": bool(supports_user and opposes_label),
    }


def create_app(entries: list[dict] | None = None, *, token: str | None = None,
               audio_base: str | None = None, audio_proxy: bool | None = None) -> FastAPI:
    entries = entries if entries is not None else load_manifest()
    audio_base = (audio_base if audio_base is not None
                  else os.environ.get("TT_AUDIO_BASE", "")).rstrip("/")
    if audio_proxy is None:
        audio_proxy = os.environ.get("TT_AUDIO_PROXY", "") not in ("", "0", "false", "no")
    if audio_proxy and not audio_base:
        raise ValueError("TT_AUDIO_PROXY needs TT_AUDIO_BASE to know what to proxy")
    # In proxy mode the page keeps fetching same-origin URLs; only this process
    # talks to the remote host.
    payload_base = "" if audio_proxy else audio_base
    by_id = {e["id"]: e for e in entries}
    by_tier: dict[str, list[dict]] = {}
    for e in entries:
        by_tier.setdefault(e["difficulty"], []).append(e)
    default_pool = [e for e in entries if e["difficulty"] in DEFAULT_POOL]
    tagged_pool = [e for e in entries if e["difficulty"] in TAGGED_POOL]
    if not default_pool:
        raise ValueError("no puzzles in the default serving pool")

    prefix = f"/{token}" if token else ""
    app = FastAPI(title="Tonic Trainer", docs_url=None, redoc_url=None)
    app.state.prefix = prefix
    app.state.token = token
    app.state.audio_base = audio_base
    app.state.audio_proxy = audio_proxy
    router = APIRouter()

    @router.get("/api/puzzle")
    def get_puzzle(tier: str | None = None) -> JSONResponse:
        if tier in (None, "", "any", "default"):
            pool = default_pool
        elif tier == "tagged":
            # The default now includes untagged; this is how it is turned off.
            pool = tagged_pool
        elif tier in by_tier:
            pool = by_tier[tier]
        else:
            raise HTTPException(status_code=404, detail=f"unknown tier {tier!r}")
        if not pool:
            raise HTTPException(status_code=404, detail=f"no puzzles in tier {tier!r}")
        # secrets.choice, not random.choice: no seeded sequence to predict, and
        # nothing about the previous call is remembered.
        return JSONResponse(_puzzle_payload(secrets.choice(pool), prefix, payload_base))

    @router.post("/api/guess")
    def post_guess(guess: Guess) -> JSONResponse:
        entry = by_id.get(guess.id)
        if entry is None:
            raise HTTPException(status_code=404, detail="unknown puzzle id")
        bucket = classify(guess.tonic_pc, guess.mode, entry["tonic_pc"], entry["mode"])
        from .normalize import display_label

        guess_display = display_label(guess.tonic_pc, guess.mode)
        return JSONResponse(
            {
                "correct": bucket == "exact",
                "actual_tonic_pc": entry["tonic_pc"],
                "actual_mode": entry["mode"],
                "key_display": entry["key_display"],
                "relative_error": bucket,
                "explanation": explain(bucket, entry["key_display"], guess_display),
            }
        )

    @router.post("/api/dispute")
    async def post_dispute(guess: Guess) -> JSONResponse:
        entry = by_id.get(guess.id)
        if entry is None:
            raise HTTPException(status_code=404, detail="unknown puzzle id")
        verdict = await run_in_threadpool(triage_dispute, entry, guess.tonic_pc, guess.mode)
        record = {
            "ts": time.time(),
            "id": entry["id"],
            "label_tonic_pc": entry["tonic_pc"],
            "label_mode": entry["mode"],
            "user_tonic_pc": guess.tonic_pc,
            "user_mode": guess.mode,
            **verdict,
        }
        # Operator-side corpus log, not user memory (SPEC §0.5).
        with DISPUTED_LOG.open("a") as fh:
            fh.write(json.dumps(record) + "\n")
        return JSONResponse({"logged": True, "escalated": verdict["escalate"]})

    @router.get("/audio/{path:path}")
    def get_audio(path: str, request: Request) -> Response:
        target = (CLIP_ROOT / path).resolve()
        try:
            target.relative_to(CLIP_ROOT.resolve())
        except ValueError:
            raise HTTPException(status_code=404, detail="not found") from None
        if not target.is_file():
            if not audio_proxy:
                raise HTTPException(status_code=404, detail="not found")
            # Local clips may have been deleted after publication; fetch from the
            # remote host and serve it from here.
            target = fetch_upstream(path, audio_base)
        return _serve_range(target, request.headers.get("range"))

    @router.get("/config.json")
    def config() -> JSONResponse:
        """Override the static config.json when this server is the one serving.

        The same docs/ tree is published to GitHub Pages, where config.json
        points at the Hugging Face CDN. When this process serves it instead —
        the demoted fallback role — the page must fetch audio from here, so the
        base is emptied and the API-only features are switched back on.
        """
        return JSONResponse({
            "audio_base": audio_base if (audio_base and not audio_proxy) else "",
            "api": True,
            "note": "served by the fallback FastAPI server, not GitHub Pages",
        })

    @router.get("/api/health")
    def health() -> JSONResponse:
        return JSONResponse({"ok": True, "puzzles": len(entries), "pool": len(default_pool)})

    app.include_router(router, prefix=prefix)
    app.mount(prefix or "/", StaticFiles(directory=str(WEB), html=True), name="web")
    return app


# ----------------------------------------------------------------- CLI


def local_ip() -> str:
    """Best-effort LAN address. UDP connect() sends nothing on the wire."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.connect(("192.0.2.1", 80))  # TEST-NET-1, never routed
        return s.getsockname()[0]


def print_qr(url: str) -> None:
    import qrcode

    qr = qrcode.QRCode(border=1)
    qr.add_data(url)
    qr.make(fit=True)
    qr.print_ascii(out=sys.stdout, invert=True)
    print(url)


def start_tunnel(port: int) -> str:
    proc = subprocess.Popen(
        ["cloudflared", "tunnel", "--url", f"http://127.0.0.1:{port}"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
    )
    deadline = time.time() + 60
    assert proc.stdout is not None
    while time.time() < deadline:
        line = proc.stdout.readline()
        if not line:
            break
        match = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", line)
        if match:
            return match.group(0)
    proc.kill()
    raise RuntimeError("cloudflared did not report a tunnel URL within 60s")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Tonic Trainer server")
    parser.add_argument("--lan", action="store_true", help="bind 0.0.0.0 and print a QR code")
    parser.add_argument("--tunnel", action="store_true",
                        help="public HTTPS via cloudflared; requires --confirm-public")
    parser.add_argument("--confirm-public", action="store_true",
                        help="acknowledge that --tunnel publishes CC audio to a public URL")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--host", default=None)
    parser.add_argument("--audio-base", default=None,
                        help="serve clips from a remote host (overrides TT_AUDIO_BASE)")
    parser.add_argument("--audio-proxy", action="store_true",
                        help="fetch remote clips server-side and serve them same-origin, "
                             "so the remote host's CORS policy does not matter")
    args = parser.parse_args(argv)

    if args.tunnel and not args.confirm_public:
        print("REFUSED: --tunnel publishes CC-licensed audio on a public URL.")
        print("That is a legal judgment call for you, not for this program. If you want it,")
        print("re-run with --tunnel --confirm-public. Prefer --lan: nothing leaves the network.")
        return 2

    exposed = args.lan or args.tunnel
    token = secrets.token_urlsafe(16) if exposed else None
    host = args.host or ("0.0.0.0" if exposed else "127.0.0.1")  # noqa: S104 — opt-in only

    app = create_app(token=token, audio_base=args.audio_base,
                     audio_proxy=True if args.audio_proxy else None)
    prefix = app.state.prefix
    if app.state.audio_base:
        mode = "proxied server-side" if app.state.audio_proxy else "fetched by the browser"
        print(f"audio: {app.state.audio_base} ({mode})")
        if not app.state.audio_proxy:
            # Direct fetch is the only mode whose viability depends on someone
            # else's headers, so it is the only one worth probing at boot.
            from .manifest import load_manifest

            sample = load_manifest()[0]["audio_path"]
            verdict = check_remote_audio(app.state.audio_base, sample)
            if verdict["ok"]:
                print(f"  CORS check OK — final hop {verdict['final_url'][:70]}... "
                      f"sends Access-Control-Allow-Origin: {verdict['acao']}")
                if verdict["acao"] != "*":
                    print("  NOTE: that is a reflected origin, not a wildcard. It may behave")
                    print("        differently from a LAN address than from localhost.")
            else:
                reason = verdict["error"] or (
                    f"HTTP {verdict['status']}, Access-Control-Allow-Origin: {verdict['acao']!r}")
                print(f"  WARNING: the remote host may not be browser-readable — {reason}")
                print("  Clips will fail to decode in the page. Re-run with --audio-proxy to")
                print("  fetch them server-side and serve them same-origin instead.")

    if args.tunnel:
        print("starting cloudflared quick tunnel ...")
        base = start_tunnel(args.port)
    elif args.lan:
        base = f"http://{local_ip()}:{args.port}"
    else:
        base = f"http://127.0.0.1:{args.port}"

    url = f"{base}{prefix}/"
    print()
    if exposed:
        print("Access is limited to this URL — the random path prefix is the token;")
        print("requests without it are 404. Rotates on every restart.")
        print_qr(url)
    else:
        print(f"Tonic Trainer on {url}")
    print()

    import uvicorn

    uvicorn.run(app, host=host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
