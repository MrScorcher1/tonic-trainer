"""Manifest construction: tiering, attribution, and leak-word exclusion."""

import pandas as pd
import pytest

from tonic_trainer.manifest import (
    DEFAULT_POOL,
    KEY_NAME_IN_TEXT,
    LEAK_WORDS,
    TAGGED_POOL,
    assign_tier,
    build_manifest,
    key_distribution,
    puzzle_id,
    served_pool,
)


@pytest.mark.parametrize(
    "genre,mode,expected",
    [
        ("Rock", "major", "tier1"),
        ("Folk", "major", "tier1"),
        ("Country", "major", "tier1"),
        ("Rock", "minor", "tier2"),
        ("Blues", "minor", "tier2"),
        ("Electronic", "major", "tier3"),
        ("Hip-Hop", "minor", "tier3"),
        (None, "major", "untagged"),
        (float("nan"), "minor", "untagged"),
        ("", "major", "untagged"),
    ],
)
def test_tiering_is_a_static_genre_prior(genre, mode, expected):
    assert assign_tier(genre, mode) == expected


def test_puzzle_id_is_zero_padded():
    assert puzzle_id(2) == "fma-000002"
    assert puzzle_id(124911) == "fma-124911"


def _row(track_id, title="A Song", artist="An Artist", genre="Rock", mode="major", usable=True):
    return {
        "track_id": track_id, "tonic_pc": 7, "mode": mode,
        "key_label_original": "G Major", "key_label_display": "G Major",
        "title": title, "artist": artist, "license": "CC BY-SA 4.0",
        "license_canonical": "CC BY-SA", "genre_top": genre, "usable": usable,
    }


def test_unusable_rows_and_rows_without_clips_never_become_puzzles():
    df = pd.DataFrame([_row(1), _row(2, usable=False), _row(3)])
    entries = build_manifest(df, {1: "000/000001.mp3"})
    assert [e["id"] for e in entries] == ["fma-000001"]


def test_attribution_leak_words_are_dropped(capsys):
    df = pd.DataFrame([
        _row(1, title="Modern Man"),
        _row(2, artist="Miracles of Modern Science"),
        _row(3, title="Prelude In F Major"),
        _row(4, title="Clean Title"),
    ])
    clips = {i: f"000/00000{i}.mp3" for i in range(1, 5)}
    entries = build_manifest(df, clips)
    assert [e["id"] for e in entries] == ["fma-000004"]
    assert "dropped 3 tracks" in capsys.readouterr().out


def test_leak_patterns_match_what_the_gates_look_for():
    assert LEAK_WORDS.search("Airplane Mode")
    assert LEAK_WORDS.search("tonic_pc")
    assert not LEAK_WORDS.search("Ordinary Title")
    assert KEY_NAME_IN_TEXT.search("Prelude In F Major")
    assert KEY_NAME_IN_TEXT.search("Study in C minor")
    # Capital "Minor" is the case the original case-sensitive rule missed, on a
    # track that really is in D minor.
    assert KEY_NAME_IN_TEXT.search("Prelude In D Minor")
    assert KEY_NAME_IN_TEXT.search("Nocturne in Bb Major")
    assert not KEY_NAME_IN_TEXT.search("Major Tom")       # no note letter in front
    assert not KEY_NAME_IN_TEXT.search("The Minor Thirds")  # "e" is the tail of "The"
    assert not KEY_NAME_IN_TEXT.search("Sea Minor")         # "a" is the tail of "Sea"


def test_manifest_rejects_a_row_that_cannot_be_attributed():
    df = pd.DataFrame([_row(1, title="   ")])
    with pytest.raises(ValueError):
        build_manifest(df, {1: "000/000001.mp3"})


def test_empty_manifest_is_an_error_not_an_empty_list():
    df = pd.DataFrame([_row(1, usable=False)])
    with pytest.raises(ValueError):
        build_manifest(df, {})


# REMOVAL-OK: test_served_pool_excludes_untagged_by_default asserted the old
# default. Untagged is now served by default (see the DEFAULT_POOL note in
# manifest.py); the two tests below cover both the new default and the filter
# that restores the old behaviour.
def test_served_pool_includes_untagged_by_default():
    entries = [
        {"difficulty": "tier1", "key_display": "C Major"},
        {"difficulty": "tier3", "key_display": "A minor"},
        {"difficulty": "untagged", "key_display": "C Major"},
    ]
    pool = served_pool(entries)
    assert len(pool) == 3
    assert all(e["difficulty"] in DEFAULT_POOL for e in pool)


def test_tagged_pool_excludes_untagged():
    entries = [
        {"difficulty": "tier1", "key_display": "C Major"},
        {"difficulty": "untagged", "key_display": "C Major"},
    ]
    assert [e["difficulty"] for e in served_pool(entries, TAGGED_POOL)] == ["tier1"]
    assert served_pool(entries, ("untagged",))[0]["difficulty"] == "untagged"


def test_key_distribution_counts_display_labels():
    dist = key_distribution([
        {"key_display": "C Major"}, {"key_display": "C Major"}, {"key_display": "A minor"},
    ])
    assert dist["C Major"] == 2
    assert dist["A minor"] == 1
