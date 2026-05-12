"""ARRIRAW reader scaffold (no ARRI SDK in CI)."""

from __future__ import annotations

from pathlib import Path

import pytest

from forge_io import read_metadata
from forge_io._registry import get_reader
from forge_io.exceptions import ArriSdkUnavailableError
from forge_io.readers import arri_reader
from forge_io.readers.arri_reader import (
    FORGE_ARRI_SDK_PATH_ENV,
    ArriRawReader,
    _arri_sdk_available,
)


def test_get_reader_selects_arri_for_ari(tmp_path: Path) -> None:
    path = tmp_path / "clip.ari"
    path.write_bytes(b"")
    r = get_reader(path)
    assert isinstance(r, ArriRawReader)


def test_sdk_unavailable_when_env_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(FORGE_ARRI_SDK_PATH_ENV, raising=False)
    assert _arri_sdk_available() is False


def test_sdk_unavailable_when_env_points_at_missing_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(FORGE_ARRI_SDK_PATH_ENV, str(tmp_path / "no-such.dylib"))
    assert _arri_sdk_available() is False


def test_sdk_unavailable_when_env_points_at_non_library(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    bogus = tmp_path / "not_a_lib.txt"
    bogus.write_text("definitely not a shared object")
    monkeypatch.setenv(FORGE_ARRI_SDK_PATH_ENV, str(bogus))
    assert _arri_sdk_available() is False


def test_sdk_available_with_real_shared_library(monkeypatch: pytest.MonkeyPatch) -> None:
    """Smoke: ctypes.CDLL succeeds against any real on-disk shared library.

    Probes numpy's installed package directory for a compiled extension (.so /
    .dylib / .pyd). numpy is a hard dependency of forge-io, so at least one
    such file must exist wherever the test suite runs.
    """
    import numpy

    np_dir = Path(numpy.__file__).parent
    candidates: list[Path] = []
    for pattern in ("*.so", "*.dylib", "*.pyd"):
        candidates.extend(p for p in np_dir.rglob(pattern) if p.is_file())
    if not candidates:
        pytest.skip("no compiled extension found in the numpy install")
    monkeypatch.setenv(FORGE_ARRI_SDK_PATH_ENV, str(candidates[0]))
    assert _arri_sdk_available() is True


def test_read_metadata_ari_raises_with_actionable_message(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv(FORGE_ARRI_SDK_PATH_ENV, raising=False)
    path = tmp_path / "clip.ari"
    path.write_bytes(b"")
    with pytest.raises(ArriSdkUnavailableError, match=FORGE_ARRI_SDK_PATH_ENV):
        read_metadata(path)


def test_read_ari_raises_when_gate_closed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Symmetry with read_metadata: pixel decode path hits the same gate."""
    import forge_io

    monkeypatch.setattr(arri_reader, "_arri_sdk_available", lambda: False)
    path = tmp_path / "clip.ari"
    path.write_bytes(b"")
    with pytest.raises(ArriSdkUnavailableError):
        forge_io.read(path)


def test_read_ari_not_implemented_when_gate_open(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """With the gate forced open, decode path raises NotImplementedError (SDK not wired)."""
    import forge_io

    monkeypatch.setattr(arri_reader, "_arri_sdk_available", lambda: True)
    path = tmp_path / "clip.ari"
    path.write_bytes(b"")
    with pytest.raises(NotImplementedError):
        forge_io.read(path)
    with pytest.raises(NotImplementedError):
        forge_io.read_metadata(path)
