"""Unit tests for key-string normalization (SPEC Gate 1)."""

import pytest

from tonic_trainer.normalize import (
    DISPLAY_SPELLING,
    MODES,
    SOURCE_SPELLING,
    display_label,
    parse_key_label,
    source_label,
)


def test_flat_and_sharp_spellings_map_to_expected_pitch_classes():
    # The gate names these two explicitly: the source uses both conventions.
    assert parse_key_label("Bb Major") == (10, "major")
    assert parse_key_label("D# Major") == (3, "major")


def test_enharmonic_pairs_agree():
    for sharp, flat in [("C#", "Db"), ("D#", "Eb"), ("F#", "Gb"), ("G#", "Ab"), ("A#", "Bb")]:
        assert parse_key_label(f"{sharp} minor") == parse_key_label(f"{flat} minor")


def test_mode_casing_is_normalized():
    assert parse_key_label("A minor")[1] == "minor"
    assert parse_key_label("A MINOR")[1] == "minor"
    assert parse_key_label("C Major")[1] == "major"


@pytest.mark.parametrize("label", ["", "H Major", "C Lydian", "C", "C sharp Major", "  "])
def test_unparseable_labels_raise(label):
    with pytest.raises((ValueError, TypeError)):
        parse_key_label(label)


def test_source_table_is_a_bijection_over_24_pairs():
    assert len(SOURCE_SPELLING) == 24
    assert len(set(SOURCE_SPELLING.values())) == 24
    for (pc, mode), label in SOURCE_SPELLING.items():
        assert parse_key_label(label) == (pc, mode)
        assert source_label(pc, mode) == label


def test_display_table_covers_all_pairs_and_parses_back():
    assert len(DISPLAY_SPELLING) == 24
    for pc in range(12):
        for mode in MODES:
            label = display_label(pc, mode)
            assert parse_key_label(label) == (pc, mode)


def test_display_uses_conventional_spelling_where_source_does_not():
    assert display_label(3, "major") == "Eb Major"  # source says D# Major
    assert display_label(3, "minor") == "Eb minor"  # source says D# minor
    assert display_label(1, "major") == "Db Major"  # source says C# Major
    assert display_label(8, "major") == "Ab Major"  # source says G# Major
    assert display_label(1, "minor") == "C# minor"  # unchanged: already conventional
    assert display_label(10, "major") == "Bb Major"
