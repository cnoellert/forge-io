"""Sony X-OCN policy: no MXF reader; public exception is importable."""

from __future__ import annotations

from pathlib import Path

import pytest

from forge_io import SonyUnsupportedError
from forge_io._registry import get_reader
from forge_io.exceptions import ForgeIOError, UnsupportedFileError


def test_sony_unsupported_error_is_forge_io_error() -> None:
    assert issubclass(SonyUnsupportedError, ForgeIOError)


def test_no_reader_registered_for_mxf(tmp_path: Path) -> None:
    p = tmp_path / "clip.mxf"
    p.write_bytes(b"")
    with pytest.raises(UnsupportedFileError):
        get_reader(p)
