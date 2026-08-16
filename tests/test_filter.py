"""License classification tests (SPEC §1.4)."""

import pytest

from tonic_trainer.filter import (
    CC0,
    CC_BY,
    CC_BY_NC,
    CC_BY_NC_ND,
    CC_BY_NC_SA,
    CC_BY_ND,
    CC_BY_SA,
    PUBLIC_DOMAIN,
    UNKNOWN,
    classify_license,
    has_nd_marker,
    published_fma_sha1,
)


@pytest.mark.parametrize(
    "text",
    [
        "Attribution-NonCommercial-NoDerivatives (aka Music Sharing) 3.0 International",
        "Attribution-Noncommercial-No Derivative Works 3.0 United States",
        "Attribution-NonCommercial-NoDerivs 3.0 Poland",
        "Attribution-NoDerivatives 4.0 International",
        "Attribution-NoDerivs 2.5 Canada",
        "Attribution-No Derivative Works 2.5 Italy",
        "Creative Commons Attribution-NonCommercial-NoDerivatives 4.0",
        "Music Sharing",  # FMA's own nickname for BY-NC-ND
        "CC BY-NC-ND 4.0",
    ],
)
def test_every_nd_spelling_is_caught(text):
    verdict = classify_license(text)
    assert verdict.is_nd, text
    assert not verdict.allowed


@pytest.mark.parametrize(
    "text",
    [
        # A bare case-insensitive "nd" substring match would wrongly exclude
        # all of these — 20 such strings exist in the real corpus.
        "Attribution 3.0 Netherlands",
        "Attribution 1.0 Finland",
        "Attribution 2.0 UK: England",
        "Attribution-Noncommercial 2.5 Switzerland",
        "Attribution-Noncommercial 3.0 New Zealand",
        "Attribution-Noncommercial-Share Alike 2.5 UK: Scotland",
        "Public Domain / Sound Recording Common Law Protection",
    ],
)
def test_place_names_are_not_mistaken_for_noderivatives(text):
    verdict = classify_license(text)
    assert not verdict.is_nd, text
    assert verdict.allowed, verdict.reason


@pytest.mark.parametrize(
    "text,canonical",
    [
        ("Attribution", CC_BY),
        ("Attribution 3.0 International", CC_BY),
        ("Creative Commons Attribution", CC_BY),
        ("Attribution-ShareAlike 3.0 International", CC_BY_SA),
        ("Attribution-Share Alike 3.0 Netherlands", CC_BY_SA),
        ("Attribution-NonCommercial 3.0 International", CC_BY_NC),
        ("Attribution-Noncommercial-Share Alike 3.0 United States", CC_BY_NC_SA),
        ("Attribution-NonCommercial-ShareAlike", CC_BY_NC_SA),
        ("CC0 1.0 Universal", CC0),
        ("Public Domain Mark 1.0", PUBLIC_DOMAIN),
        ("Attribution-NoDerivatives 3.0 International", CC_BY_ND),
        ("Attribution-Noncommercial-NoDerivatives 2.0 France", CC_BY_NC_ND),
    ],
)
def test_canonical_identifiers(text, canonical):
    assert classify_license(text).canonical == canonical


@pytest.mark.parametrize("text", ["Free Music Philosophy", "PennSound", "ideology.de",
                                  "CopyrightPlus", "Orphan Work", "", None])
def test_unrecognized_or_missing_licenses_are_excluded_not_assumed(text):
    verdict = classify_license(text)
    assert verdict.canonical == UNKNOWN
    assert not verdict.allowed
    assert verdict.reason


def test_noncommercial_is_flagged_but_allowed_by_default():
    verdict = classify_license("Attribution-NonCommercial 3.0 International")
    assert verdict.is_nc
    assert verdict.allowed


def test_has_nd_marker_helper_agrees_with_classification():
    assert has_nd_marker("Attribution-NoDerivs 2.5 Canada")
    assert not has_nd_marker("Attribution 3.0 Netherlands")


def test_published_sha1_is_read_from_the_readme_not_assumed():
    readme = (
        "Download the archives:\n"
        '    echo "f0df49ffe5f2a6008d7dc83c6915b31835dfe733  fma_metadata.zip" | sha1sum -c -\n'
        '    echo "497109f4dd721066b5ce5e5f250ec604dc78939e  fma_large.zip"    | sha1sum -c -\n'
    )
    assert published_fma_sha1("fma_metadata.zip", readme) == "f0df49ffe5f2a6008d7dc83c6915b31835dfe733"
    assert published_fma_sha1("fma_large.zip", readme) == "497109f4dd721066b5ce5e5f250ec604dc78939e"
    with pytest.raises(ValueError):
        published_fma_sha1("fma_tiny.zip", readme)
