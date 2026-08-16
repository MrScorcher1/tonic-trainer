"""Manifest construction: genre labelling, difficulty attachment, leak drops.

The tier scheme these tests used to cover is gone — it claimed a difficulty
ranking that measurement inverted. `genre` is now a filter and `difficulty` is
computed per song from the audio, so the tests follow.

REMOVAL-OK: test_tiering_is_a_static_genre_prior and the pool tests covered
`assign_tier` and DEFAULT_POOL/TAGGED_POOL, which no longer exist. Their
replacements below cover the fields that took over: genre labelling, the refusal
to publish an entry without a computed difficulty, and the two independent
filters.
"""

import pandas as pd
import pytest

from tonic_trainer.manifest import (
    KEY_NAME_IN_TEXT,
    LEAK_WORDS,
    UNGENRED,
    attach_difficulty,
    build_manifest,
    genre_label,
    key_distribution,
    puzzle_id,
    served_pool,
)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Rock", "Rock"),
        ("Hip-Hop", "Hip-Hop"),
        ("  Folk  ", "Folk"),
        (None, UNGENRED),
        (float("nan"), UNGENRED),
        ("", UNGENRED),
        ("   ", UNGENRED),
    ],
)
def test_genre_label_is_the_real_genre_or_ungenred(raw, expected):
    assert genre_label(raw) == expected


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


def test_entries_carry_genre_and_no_tier():
    df = pd.DataFrame([_row(1), _row(2, genre=None)])
    entries = build_manifest(df, {1: "000/000001.mp3", 2: "000/000002.mp3"})
    assert [e["genre"] for e in entries] == ["Rock", UNGENRED]
    assert all("difficulty" not in e for e in entries)   # attached separately
    assert all("genre_top" not in e for e in entries)


def test_unusable_rows_and_rows_without_clips_never_become_puzzles():
    df = pd.DataFrame([_row(1), _row(2, usable=False), _row(3)])
    entries = build_manifest(df, {1: "000/000001.mp3"})
    assert [e["id"] for e in entries] == ["fma-000001"]


def test_attach_difficulty_refuses_to_publish_an_unrated_entry():
    entries = [{"id": "fma-000001"}, {"id": "fma-000002"}]
    with pytest.raises(ValueError, match="no computed difficulty"):
        attach_difficulty(entries, {"fma-000001": 2})

    rated = attach_difficulty(entries, {"fma-000001": 2, "fma-000002": 3})
    assert [e["difficulty"] for e in rated] == [2, 3]
    assert all(isinstance(e["difficulty"], int) for e in rated)


def test_attribution_leak_words_are_dropped(capsys):
    df = pd.DataFrame([
        _row(1, title="Modern Man"),
        _row(2, artist="Miracles of Modern Science"),
        _row(3, title="Prelude In F Major"),
        _row(4, title="Prelude In D Minor"),
        _row(5, title="Clean Title"),
    ])
    clips = {i: f"000/00000{i}.mp3" for i in range(1, 6)}
    entries = build_manifest(df, clips)
    assert [e["id"] for e in entries] == ["fma-000005"]
    assert "dropped 4 tracks" in capsys.readouterr().out


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
    assert not KEY_NAME_IN_TEXT.search("Major Tom")         # no note letter in front
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


ENTRIES = [
    {"id": "a", "genre": "Rock", "difficulty": 1, "key_display": "C Major"},
    {"id": "b", "genre": "Rock", "difficulty": 3, "key_display": "A minor"},
    {"id": "c", "genre": UNGENRED, "difficulty": 2, "key_display": "C Major"},
]


def test_served_pool_defaults_to_the_whole_corpus():
    assert len(served_pool(ENTRIES)) == 3


def test_genre_and_difficulty_are_independent_filters():
    assert [e["id"] for e in served_pool(ENTRIES, genres=["Rock"])] == ["a", "b"]
    assert [e["id"] for e in served_pool(ENTRIES, levels=[1, 2])] == ["a", "c"]
    assert [e["id"] for e in served_pool(ENTRIES, genres=["Rock"], levels=[3])] == ["b"]
    # The old "genre-labelled only" behaviour is just a genre selection now.
    labelled = served_pool(ENTRIES, genres=[g for g in {"Rock"} if g != UNGENRED])
    assert UNGENRED not in [e["genre"] for e in labelled]


def test_key_distribution_counts_display_labels():
    dist = key_distribution(ENTRIES)
    assert dist["C Major"] == 2
    assert dist["A minor"] == 1
