"""`assume_source` is fill-unknown-only: authoritative declared colorspace wins.

Covers the behavior matrix from the fill-unknown-only change — a pure-function
test, no OCIO/PyOpenColorIO dependency.
"""

from __future__ import annotations

import pytest

from forge_io._color import effective_source_colorspace
from forge_io._types import UNKNOWN_COLORSPACE


def test_authoritative_declared_wins_when_no_hint() -> None:
    assert effective_source_colorspace("ACES2065-1", None) == "ACES2065-1"


def test_authoritative_declared_wins_over_conflicting_hint() -> None:
    """The fix: a raw decode's canonical CS is not overridden by a caller hint."""
    assert effective_source_colorspace("ACES2065-1", "ARRI LogC4") == "ACES2065-1"
    assert effective_source_colorspace("Linear REDWideGamutRGB", "REDLog3G10") == (
        "Linear REDWideGamutRGB"
    )


def test_embedded_declared_wins_over_hint() -> None:
    """An EXR/file that declares a real CS is authoritative over assume_source."""
    assert effective_source_colorspace("Rec.709", "ACEScg") == "Rec.709"


def test_hint_fills_when_declared_unknown() -> None:
    """Editorial container / DPX declaring unknown is filled by the hint."""
    assert effective_source_colorspace(UNKNOWN_COLORSPACE, "Rec.709") == "Rec.709"


def test_unknown_stays_unknown_without_hint() -> None:
    assert effective_source_colorspace(UNKNOWN_COLORSPACE, None) == UNKNOWN_COLORSPACE


def test_unknown_hint_does_not_fill() -> None:
    """A literal 'unknown' hint is treated as no hint — stays unknown."""
    assert effective_source_colorspace(UNKNOWN_COLORSPACE, UNKNOWN_COLORSPACE) == UNKNOWN_COLORSPACE


@pytest.mark.parametrize(
    ("declared", "assume", "expected"),
    [
        ("ACES2065-1", None, "ACES2065-1"),
        ("ACES2065-1", "ARRI LogC4", "ACES2065-1"),  # the fix
        (UNKNOWN_COLORSPACE, "Rec.709", "Rec.709"),
        (UNKNOWN_COLORSPACE, None, UNKNOWN_COLORSPACE),
        ("Rec.709", "ACEScg", "Rec.709"),
    ],
)
def test_behavior_matrix(declared: str, assume: str | None, expected: str) -> None:
    assert effective_source_colorspace(declared, assume) == expected
