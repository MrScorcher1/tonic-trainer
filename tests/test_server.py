"""Phase 5 server tests (SPEC Gate 5)."""

from __future__ import annotations

import json

import pytest

from tonic_trainer.scoring import classify


def test_puzzle_response_never_serializes_the_answer(client):
    # Asserted on the raw JSON string, not the parsed object: a leak through an
    # unexpected field name would survive a structural check.
    for _ in range(20):
        raw = client.get("/api/puzzle").text
        assert "tonic_pc" not in raw
        assert "mode" not in raw
        assert "key_display" not in raw


def test_puzzle_carries_attribution_and_audio(client):
    body = client.get("/api/puzzle").json()
    for field in ("id", "audio_url", "title", "artist", "license"):
        assert body[field], f"{field} is empty"
    assert body["audio_url"].startswith("/audio/")


def test_tier_filter(client, manifest):
    body = client.get("/api/puzzle", params={"tier": "tier1"}).json()
    entry = next(e for e in manifest if e["id"] == body["id"])
    assert entry["difficulty"] == "tier1"
    assert client.get("/api/puzzle", params={"tier": "nope"}).status_code == 404


# REMOVAL-OK: test_untagged_is_not_in_the_default_pool asserted the opposite of
# the behaviour the user chose after the measurement. It is inverted below rather
# than deleted, and `test_tagged_filter_turns_untagged_back_off` now covers the
# exclusion path it used to guard.
def test_untagged_is_in_the_default_pool(client, manifest):
    # The default changed once the premise behind excluding untagged was
    # measured and failed — see the note on DEFAULT_POOL in manifest.py.
    by_id = {e["id"]: e for e in manifest}
    seen = {by_id[client.get("/api/puzzle").json()["id"]]["difficulty"] for _ in range(120)}
    assert "untagged" in seen


def test_tagged_filter_turns_untagged_back_off(client, manifest):
    by_id = {e["id"]: e for e in manifest}
    seen = {
        by_id[client.get("/api/puzzle", params={"tier": "tagged"}).json()["id"]]["difficulty"]
        for _ in range(120)
    }
    assert "untagged" not in seen
    assert seen <= {"tier1", "tier2", "tier3"}


def test_correct_answer_scores_correct(client, manifest):
    entry = manifest[0]
    body = client.post("/api/guess", json={
        "id": entry["id"], "tonic_pc": entry["tonic_pc"], "mode": entry["mode"]}).json()
    assert body["correct"] is True
    assert body["relative_error"] == "exact"
    assert body["key_display"] == entry["key_display"]


def test_relative_answer_scores_relative(client, manifest):
    major = next(e for e in manifest if e["mode"] == "major")
    rel_minor_pc = (major["tonic_pc"] - 3) % 12
    body = client.post("/api/guess", json={
        "id": major["id"], "tonic_pc": rel_minor_pc, "mode": "minor"}).json()
    assert body["correct"] is False
    assert body["relative_error"] == "relative"

    minor = next(e for e in manifest if e["mode"] == "minor")
    rel_major_pc = (minor["tonic_pc"] + 3) % 12
    body = client.post("/api/guess", json={
        "id": minor["id"], "tonic_pc": rel_major_pc, "mode": "major"}).json()
    assert body["relative_error"] == "relative"


def test_guess_rejects_unknown_id_and_bad_input(client):
    assert client.post("/api/guess", json={"id": "nope", "tonic_pc": 0, "mode": "major"}).status_code == 404
    assert client.post("/api/guess", json={"id": "fma-000000", "tonic_pc": 12, "mode": "major"}).status_code == 422
    assert client.post("/api/guess", json={"id": "fma-000000", "tonic_pc": 0, "mode": "dorian"}).status_code == 422


def test_audio_supports_range_requests(client):
    url = client.get("/api/puzzle").json()["audio_url"]
    full = client.get(url)
    assert full.status_code == 200
    assert full.headers["accept-ranges"] == "bytes"
    size = len(full.content)

    partial = client.get(url, headers={"Range": "bytes=0-1023"})
    assert partial.status_code == 206
    assert partial.headers["content-range"] == f"bytes 0-1023/{size}"
    assert len(partial.content) == 1024
    assert partial.content == full.content[:1024]

    tail = client.get(url, headers={"Range": f"bytes={size - 100}-"})
    assert tail.status_code == 206
    assert tail.headers["content-range"] == f"bytes {size - 100}-{size - 1}/{size}"
    assert tail.content == full.content[-100:]

    assert client.get(url, headers={"Range": f"bytes={size + 10}-"}).status_code == 416


def test_audio_rejects_path_traversal(client):
    assert client.get("/audio/../../../etc/passwd").status_code == 404
    assert client.get("/audio/000/nope.mp3").status_code == 404


def test_fifty_calls_return_at_least_forty_distinct_ids(client):
    ids = {client.get("/api/puzzle").json()["id"] for _ in range(50)}
    assert len(ids) >= 40, f"only {len(ids)} distinct ids in 50 calls"


def test_guess_is_a_pure_function(client, manifest):
    entry = manifest[3]
    body = {"id": entry["id"], "tonic_pc": 4, "mode": "major"}
    first = client.post("/api/guess", json=body)
    for _ in range(25):
        again = client.post("/api/guess", json=body)
        assert again.content == first.content, "identical requests produced different bytes"


def test_server_holds_no_growing_per_request_state(client, manifest):
    app = client.app
    before = dict(app.state.__dict__)
    entry = manifest[5]
    for i in range(50):
        client.get("/api/puzzle")
        client.post("/api/guess", json={"id": entry["id"], "tonic_pc": i % 12, "mode": "major"})
    after = dict(app.state.__dict__)
    assert set(before) == set(after), "request handling added state to app.state"
    for key in before:
        if key in ("prefix", "token"):
            assert before[key] == after[key]
    # No cookies are ever set: nothing identifies a caller across requests.
    assert not client.cookies, f"server set cookies: {dict(client.cookies)}"


def test_token_prefix_gates_every_route(manifest, clip_dir, monkeypatch):
    from fastapi.testclient import TestClient

    from tonic_trainer import server

    monkeypatch.setattr(server, "CLIP_ROOT", clip_dir)
    app = server.create_app(manifest, token="s3cr3t-prefix")
    client = TestClient(app)

    assert client.get("/api/puzzle").status_code == 404
    body = client.get("/s3cr3t-prefix/api/puzzle")
    assert body.status_code == 200
    assert body.json()["audio_url"].startswith("/s3cr3t-prefix/audio/")
    assert client.get(body.json()["audio_url"]).status_code == 200


@pytest.mark.parametrize(
    "guess_pc,guess_mode,actual_pc,actual_mode,expected",
    [
        (0, "major", 0, "major", "exact"),
        (9, "minor", 0, "major", "relative"),
        (0, "major", 9, "minor", "relative"),
        (0, "minor", 0, "major", "parallel"),
        (1, "major", 0, "major", "semitone"),
        (11, "major", 0, "major", "semitone"),
        (7, "major", 0, "major", "fifth"),
        (5, "major", 0, "major", "fifth"),
        (6, "major", 0, "major", "other"),
        (4, "minor", 0, "major", "other"),
    ],
)
def test_relative_error_taxonomy(guess_pc, guess_mode, actual_pc, actual_mode, expected):
    assert classify(guess_pc, guess_mode, actual_pc, actual_mode) == expected


def test_tunnel_refuses_without_confirmation(capsys):
    from tonic_trainer.server import main

    assert main(["--tunnel"]) == 2
    assert "REFUSED" in capsys.readouterr().out


def test_health_reports_pool_size(client, manifest):
    body = client.get("/api/health").json()
    assert body["ok"] is True
    assert body["puzzles"] == len(manifest)
    # The default pool is the whole corpus now, untagged included.
    assert body["pool"] == len(manifest)


def test_dispute_is_logged_with_triage(client, manifest, monkeypatch, tmp_path):
    from tonic_trainer import server

    log = tmp_path / "disputed.jsonl"
    monkeypatch.setattr(server, "DISPUTED_LOG", log)
    monkeypatch.setattr(server, "triage_dispute", lambda entry, pc, mode: {
        "estimated_tonic_pc": pc, "estimated_mode": mode,
        "estimator_supports_user": True, "estimator_opposes_label": True, "escalate": True,
    })
    entry = manifest[0]
    body = client.post("/api/dispute", json={
        "id": entry["id"], "tonic_pc": 5, "mode": "minor"}).json()
    assert body == {"logged": True, "escalated": True}
    record = json.loads(log.read_text().strip())
    assert record["id"] == entry["id"]
    assert record["user_tonic_pc"] == 5


def test_audio_base_points_clips_at_a_remote_host(manifest, clip_dir, monkeypatch):
    from fastapi.testclient import TestClient

    from tonic_trainer import server

    monkeypatch.setattr(server, "CLIP_ROOT", clip_dir)
    base = "https://huggingface.co/datasets/MrScorcher1/tonic-trainer/resolve/main/clips"
    client = TestClient(server.create_app(manifest, audio_base=base + "/"))
    body = client.get("/api/puzzle").json()
    assert body["audio_url"].startswith(base + "/")
    assert body["audio_url"].endswith(".mp3")
    # Local serving still works — the remote base only changes what the page fetches.
    assert client.get("/audio/000/000001.mp3").status_code == 200


def test_local_audio_is_the_default(client):
    assert client.get("/api/puzzle").json()["audio_url"].startswith("/audio/")
