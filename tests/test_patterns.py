from __future__ import annotations

import pytest

from forge_io import resolve_pattern


def test_printf_pattern() -> None:
    assert resolve_pattern("seq.%04d.exr", 42) == "seq.0042.exr"


def test_flame_bracket_pattern() -> None:
    assert resolve_pattern("seq.[0001-0100].exr", 42) == "seq.0042.exr"


def test_flame_out_of_range() -> None:
    with pytest.raises(ValueError):
        resolve_pattern("seq.[0001-0100].exr", 200)


def test_literal_path() -> None:
    assert resolve_pattern("/tmp/foo.exr", 0) == "/tmp/foo.exr"
