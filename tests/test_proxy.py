"""Proxy mode: the browser stays same-origin, this process talks to the host.

Hugging Face's `resolve/` responses are not reliably CORS-enabled, so direct
browser fetches may be refused. Proxy mode removes the browser from that
conversation entirely, which is why it exists.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tonic_trainer import server


@pytest.fixture()
def proxy_client(manifest, clip_dir, tmp_path, monkeypatch):
    """A proxy-mode app whose local clip dir is EMPTY, so every hit goes upstream."""
    empty = tmp_path / "no-local-clips"
    empty.mkdir()
    cache = tmp_path / "cache"
    monkeypatch.setattr(server, "CLIP_ROOT", empty)
    monkeypatch.setattr(server, "AUDIO_CACHE", cache)

    fetched: list[str] = []

    class FakeResponse:
        status_code = 200

        def __init__(self, content):
            self.content = content

    def fake_get(url, timeout=None):
        fetched.append(url)
        rel = url.rsplit("/clips/", 1)[1]
        return FakeResponse((clip_dir / rel).read_bytes())

    import requests

    monkeypatch.setattr(requests, "get", fake_get)
    app = server.create_app(manifest, audio_base="https://example.invalid/clips",
                            audio_proxy=True)
    return TestClient(app), fetched, cache


def test_proxy_serves_same_origin_urls(proxy_client):
    client, _fetched, _cache = proxy_client
    url = client.get("/api/puzzle").json()["audio_url"]
    assert url.startswith("/audio/"), "in proxy mode the page must stay same-origin"


def test_proxy_fetches_upstream_once_and_caches(proxy_client):
    client, fetched, cache = proxy_client
    url = client.get("/api/puzzle").json()["audio_url"]

    first = client.get(url)
    assert first.status_code == 200
    assert len(fetched) == 1

    second = client.get(url)
    assert second.content == first.content
    assert len(fetched) == 1, "a cached clip must not be refetched"
    assert list(cache.rglob("*.mp3"))


def test_proxy_keeps_range_support_for_ios(proxy_client):
    client, _fetched, _cache = proxy_client
    url = client.get("/api/puzzle").json()["audio_url"]
    size = len(client.get(url).content)

    part = client.get(url, headers={"Range": "bytes=0-511"})
    assert part.status_code == 206
    assert part.headers["content-range"] == f"bytes 0-511/{size}"


def test_proxy_reports_an_upstream_failure_rather_than_serving_nothing(
    manifest, clip_dir, tmp_path, monkeypatch
):
    monkeypatch.setattr(server, "CLIP_ROOT", tmp_path / "empty")
    (tmp_path / "empty").mkdir()
    monkeypatch.setattr(server, "AUDIO_CACHE", tmp_path / "cache")

    class Missing:
        status_code = 404
        content = b""

    import requests

    monkeypatch.setattr(requests, "get", lambda url, timeout=None: Missing())
    client = TestClient(server.create_app(manifest, audio_base="https://example.invalid/clips",
                                          audio_proxy=True), raise_server_exceptions=False)
    url = client.get("/api/puzzle").json()["audio_url"]
    assert client.get(url).status_code == 502


def test_proxy_without_a_base_is_a_configuration_error(manifest):
    with pytest.raises(ValueError):
        server.create_app(manifest, audio_base="", audio_proxy=True)


def test_proxy_prefers_a_local_clip_when_one_exists(manifest, clip_dir, tmp_path, monkeypatch):
    monkeypatch.setattr(server, "CLIP_ROOT", clip_dir)
    monkeypatch.setattr(server, "AUDIO_CACHE", tmp_path / "cache")

    def explode(*_args, **_kwargs):
        raise AssertionError("upstream was called even though the clip is local")

    import requests

    monkeypatch.setattr(requests, "get", explode)
    client = TestClient(server.create_app(manifest, audio_base="https://example.invalid/clips",
                                          audio_proxy=True))
    url = client.get("/api/puzzle").json()["audio_url"]
    assert client.get(url).status_code == 200
