"""The boot-time CORS probe (SPEC follow-up: fail at startup, not at playback).

Direct remote fetch is the only mode whose viability rests on someone else's
headers. These tests pin what the probe concludes, without reaching the network.
"""

from __future__ import annotations

import pytest
import requests

from tonic_trainer.server import check_remote_audio

BASE = "https://huggingface.co/datasets/MrScorcher1/tonic-trainer/resolve/main/clips"
CDN = "https://us.aws.cdn.hf.co/repos/xx/yy/000173.mp3?Expires=1&Signature=abc"


class FakeResponse:
    def __init__(self, status_code, headers, url):
        self.status_code = status_code
        self.headers = headers
        self.url = url

    def close(self):
        pass


def test_wildcard_on_the_final_hop_is_viable(monkeypatch):
    # The real measured shape: the 302 reflects the origin, the CDN response
    # that carries the bytes sends `*`.
    monkeypatch.setattr(requests, "get", lambda *a, **k: FakeResponse(
        200, {"access-control-allow-origin": "*"}, CDN))
    verdict = check_remote_audio(BASE, "000/000173.mp3")
    assert verdict["ok"] is True
    assert verdict["acao"] == "*"
    assert verdict["final_url"] == CDN


def test_missing_header_on_the_final_hop_is_not_viable(monkeypatch):
    monkeypatch.setattr(requests, "get", lambda *a, **k: FakeResponse(200, {}, CDN))
    verdict = check_remote_audio(BASE, "000/000173.mp3")
    assert verdict["ok"] is False
    assert verdict["acao"] is None


def test_reflected_origin_is_viable_but_reported_verbatim(monkeypatch):
    monkeypatch.setattr(requests, "get", lambda *a, **k: FakeResponse(
        200, {"access-control-allow-origin": "http://localhost:8000"}, CDN))
    verdict = check_remote_audio(BASE, "000/000173.mp3")
    assert verdict["ok"] is True
    # Reported as-is so the caller can warn that a LAN origin may differ.
    assert verdict["acao"] == "http://localhost:8000"


def test_an_error_status_is_not_viable(monkeypatch):
    monkeypatch.setattr(requests, "get", lambda *a, **k: FakeResponse(
        404, {"access-control-allow-origin": "*"}, CDN))
    assert check_remote_audio(BASE, "000/000173.mp3")["ok"] is False


def test_a_network_failure_is_reported_not_swallowed(monkeypatch):
    def boom(*_a, **_k):
        raise requests.ConnectionError("blocked by allowlist")

    monkeypatch.setattr(requests, "get", boom)
    verdict = check_remote_audio(BASE, "000/000173.mp3")
    assert verdict["ok"] is False
    assert "blocked by allowlist" in verdict["error"]


@pytest.mark.parametrize("path", ["000/000173.mp3", "110/110983.mp3"])
def test_the_probe_requests_the_resolve_url_not_a_cdn_url(monkeypatch, path):
    seen: list[str] = []

    def capture(url, **_k):
        seen.append(url)
        return FakeResponse(200, {"access-control-allow-origin": "*"}, CDN)

    monkeypatch.setattr(requests, "get", capture)
    check_remote_audio(BASE, path)
    # The CDN URL is signed and expires; only the resolve/ URL may be requested.
    assert seen == [f"{BASE}/{path}"]
    assert "cdn.hf.co" not in seen[0]
