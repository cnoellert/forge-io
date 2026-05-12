"""ARRIRAW reader scaffold (no ARRI SDK in CI)."""

from __future__ import annotations

from pathlib import Path

import pytest

from forge_io import read_metadata
from forge_io._registry import get_reader
from forge_io.exceptions import ArriSdkUnavailableError
from forge_io.readers.arri_reader import ArriRawReader


def test_get_reader_selects_arri_for_ari(tmp_path: Path) -> None:
    path = tmp_path / "clip.ari"
    path.write_bytes(b"")
    r = get_reader(path)
    assert isinstance(r, ArriRawReader)


def test_read_metadata_ari_raises_until_sdk(tmp_path: Path) -> None:
    path = tmp_path / "clip.ari"
    path.write_bytes(b"")
    with pytest.raises(ArriSdkUnavailableError):
        read_metadata(path)
