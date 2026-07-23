"""Sony X-OCN policy: essence-classified MXF; X-OCN stays unsupported."""

from __future__ import annotations

from pathlib import Path

import pytest

from forge_io import SonyUnsupportedError
from forge_io._registry import get_reader
from forge_io.exceptions import ForgeIOError, UnsupportedFileError
from forge_io.readers import ffmpeg_reader


def test_sony_unsupported_error_is_forge_io_error() -> None:
    assert issubclass(SonyUnsupportedError, ForgeIOError)


def test_unclassifiable_mxf_raises_unsupported(tmp_path: Path) -> None:
    """An empty/unclassifiable `.mxf` is claimed by no reader → UnsupportedFileError.

    Since v0.5.0, `.mxf` is essence-classified: editorial → FFmpegReader,
    ARRIRAW → ArriRawReader, everything else (Sony X-OCN, empty, corrupt) →
    UnsupportedFileError. An empty file classifies as unsupported.
    """
    p = tmp_path / "clip.mxf"
    p.write_bytes(b"")
    with pytest.raises(UnsupportedFileError):
        get_reader(p)


def test_sony_xocn_essence_stays_unsupported(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A Sony X-OCN MXF (classifier returns unsupported) is not claimed by any reader."""
    monkeypatch.setattr(ffmpeg_reader, "_classify_mxf", lambda p: ffmpeg_reader._MXF_UNSUPPORTED)
    p = tmp_path / "xocn.mxf"
    p.write_bytes(b"\x00")
    with pytest.raises(UnsupportedFileError):
        get_reader(p)
