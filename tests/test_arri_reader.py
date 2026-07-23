"""ARRIRAW reader scaffold + ART-CMD subprocess backend."""

from __future__ import annotations

import stat
from pathlib import Path

import numpy as np
import pytest

from forge_io import read, read_metadata
from forge_io._registry import get_reader
from forge_io.exceptions import ArriSdkUnavailableError, UnsupportedFileError
from forge_io.readers import arri_reader, ffmpeg_reader
from forge_io.readers.arri_reader import (
    FORGE_ARRI_ART_PATH_ENV,
    FORGE_ARRI_SDK_PATH_ENV,
    ArriRawReader,
    _arri_art_available,
    _arri_art_path,
    _arri_sdk_available,
)
from forge_io.readers.ffmpeg_reader import FFmpegReader

# Private fixture location for live ART-CMD end-to-end tests. Gitignored.
# Drop or symlink a real ARRIRAW HDE clip directory under
# tests/fixtures/private/arri/<clip_name>/<clip_name>.NNNNNNN.arx and the e2e
# tests will run. Otherwise they skip cleanly.
PRIVATE_ARRI_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "private" / "arri"

# Private fixture location for live MXF-wrapped ARRIRAW e2e. Drop or symlink a
# real single-file ARRIRAW .mxf (e.g. an ALEXA 35 clip) directly under
# tests/fixtures/private/arri_mxf/*.mxf. Gitignored; skips cleanly if absent.
PRIVATE_ARRI_MXF_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "private" / "arri_mxf"


# ---------- dispatch + extension handling --------------------------------


def test_get_reader_selects_arri_for_ari(tmp_path: Path) -> None:
    path = tmp_path / "clip.ari"
    path.write_bytes(b"")
    assert isinstance(get_reader(path), ArriRawReader)


def test_get_reader_selects_arri_for_arx(tmp_path: Path) -> None:
    """HDE-compressed single-frame .arx must dispatch to the ARRI reader."""
    path = tmp_path / "clip.arx"
    path.write_bytes(b"")
    assert isinstance(get_reader(path), ArriRawReader)


def test_get_reader_extension_is_case_insensitive(tmp_path: Path) -> None:
    path = tmp_path / "CLIP.ARX"
    path.write_bytes(b"")
    assert isinstance(get_reader(path), ArriRawReader)


# ---------- SDK gate (option 1 backend) ----------------------------------


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
    """ctypes.CDLL succeeds against any real on-disk shared library (numpy ships some)."""
    import numpy

    np_dir = Path(numpy.__file__).parent
    candidates: list[Path] = []
    for pattern in ("*.so", "*.dylib", "*.pyd"):
        candidates.extend(p for p in np_dir.rglob(pattern) if p.is_file())
    if not candidates:
        pytest.skip("no compiled extension found in the numpy install")
    monkeypatch.setenv(FORGE_ARRI_SDK_PATH_ENV, str(candidates[0]))
    assert _arri_sdk_available() is True


# ---------- ART-CMD gate (option 2 backend) ------------------------------


def test_art_unavailable_when_env_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(FORGE_ARRI_ART_PATH_ENV, raising=False)
    assert _arri_art_path() is None
    assert _arri_art_available() is False


def test_art_unavailable_when_env_points_at_missing_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(FORGE_ARRI_ART_PATH_ENV, str(tmp_path / "no-such-bin"))
    assert _arri_art_path() is None


def test_art_unavailable_when_env_points_at_non_executable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    not_exec = tmp_path / "art-cmd"
    not_exec.write_text("#!/bin/sh\necho hi\n")
    # Deliberately no chmod +x — file is not executable.
    monkeypatch.setenv(FORGE_ARRI_ART_PATH_ENV, str(not_exec))
    assert _arri_art_path() is None


def test_art_available_when_env_points_at_executable_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = tmp_path / "art-cmd"
    fake.write_text("#!/bin/sh\nexit 0\n")
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    monkeypatch.setenv(FORGE_ARRI_ART_PATH_ENV, str(fake))
    assert _arri_art_path() == fake
    assert _arri_art_available() is True


# ---------- backend selection + error messaging --------------------------


def test_read_ari_raises_when_both_gates_closed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """No SDK + no ART-CMD → actionable error naming both env vars."""
    monkeypatch.delenv(FORGE_ARRI_SDK_PATH_ENV, raising=False)
    monkeypatch.delenv(FORGE_ARRI_ART_PATH_ENV, raising=False)
    path = tmp_path / "clip.ari"
    path.write_bytes(b"")
    with pytest.raises(ArriSdkUnavailableError) as ei:
        read(path)
    msg = str(ei.value)
    assert FORGE_ARRI_SDK_PATH_ENV in msg
    assert FORGE_ARRI_ART_PATH_ENV in msg


def test_read_metadata_arx_raises_when_both_gates_closed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Symmetry with read(): metadata path also names both env vars."""
    monkeypatch.delenv(FORGE_ARRI_SDK_PATH_ENV, raising=False)
    monkeypatch.delenv(FORGE_ARRI_ART_PATH_ENV, raising=False)
    path = tmp_path / "clip.arx"
    path.write_bytes(b"")
    with pytest.raises(ArriSdkUnavailableError) as ei:
        read_metadata(path)
    assert FORGE_ARRI_ART_PATH_ENV in str(ei.value)


def test_sdk_gate_open_raises_not_implemented(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """SDK gate takes precedence over ART-CMD; sibling package not yet implemented."""
    monkeypatch.setattr(arri_reader, "_arri_sdk_available", lambda: True)
    monkeypatch.setattr(arri_reader, "_arri_art_path", lambda: None)
    path = tmp_path / "clip.ari"
    path.write_bytes(b"")
    with pytest.raises(NotImplementedError):
        read(path)
    with pytest.raises(NotImplementedError):
        read_metadata(path)


def test_art_backend_invoked_when_sdk_closed_and_art_open(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """When only ART-CMD is configured, read() dispatches to the ART backend."""
    monkeypatch.setattr(arri_reader, "_arri_sdk_available", lambda: False)
    sentinel = tmp_path / "fake-art"
    monkeypatch.setattr(arri_reader, "_arri_art_path", lambda: sentinel)

    called: dict[str, object] = {}

    def fake_decode(art: Path, path: Path) -> object:
        called["art"] = art
        called["path"] = path
        raise RuntimeError("decode-stub-reached")

    monkeypatch.setattr(arri_reader, "_decode_via_art_cmd", fake_decode)
    clip = tmp_path / "clip.arx"
    clip.write_bytes(b"")
    with pytest.raises(RuntimeError, match="decode-stub-reached"):
        read(clip)
    assert called["art"] == sentinel
    assert Path(str(called["path"])).name == "clip.arx"


# ---------- ART-CMD sequence helpers (unit) -------------------------------


def test_parse_frame_number_handles_ari_and_arx(tmp_path: Path) -> None:
    p_ari = tmp_path / "A001C046_130101_NQ96.0189326.ari"
    p_arx = tmp_path / "A001C046_130101_NQ96.0189326.arx"
    for p in (p_ari, p_arx):
        p.write_bytes(b"")
        assert arri_reader._parse_frame_number(p) == 189326


def test_clip_min_frame_finds_lowest(tmp_path: Path) -> None:
    base = "A001C046_130101_NQ96"
    for n in (189327, 189326, 189330, 189328):
        (tmp_path / f"{base}.{n:07d}.arx").write_bytes(b"")
    assert arri_reader._clip_min_frame(tmp_path, base, ".arx") == 189326


def test_printf_pattern_preserves_padding(tmp_path: Path) -> None:
    p = tmp_path / "A001C046_130101_NQ96.0189326.arx"
    p.write_bytes(b"")
    assert arri_reader._printf_pattern_for(p) == "A001C046_130101_NQ96.%07d.arx"


def test_parse_fraction_handles_art_cmd_strings() -> None:
    assert arri_reader._parse_fraction("24/1") == 24.0
    assert arri_reader._parse_fraction("217/15625") == pytest.approx(217 / 15625)
    assert arri_reader._parse_fraction("1/1") == 1.0
    assert arri_reader._parse_fraction("1/0") is None
    assert arri_reader._parse_fraction("not a fraction") is None
    assert arri_reader._parse_fraction(24.0) is None
    assert arri_reader._parse_fraction(None) is None


def test_canonical_from_metadata_json_synthetic() -> None:
    """Synthetic ART-CMD export shape — runs without the real fixture."""
    doc = {
        "clipBasedMetadataSets": [
            {
                "metadataSetName": "Image Size",
                "metadataSetPayload": {
                    "storedSize": {"width": 4448, "height": 3096},
                },
            },
            {
                "metadataSetName": "Project Rate",
                "metadataSetPayload": {"dropframe": False, "timebase": "24/1"},
            },
        ],
        "descriptiveMetadataSets": [
            {
                "metadataSetName": "Lens Device",
                "metadataSetPayload": {"lensSqueezeFactor": "1/1"},
            },
        ],
    }
    canonical = arri_reader._canonical_from_metadata_json(doc)
    assert canonical["resolution"] == (4448, 3096)
    assert canonical["framerate"] == 24.0
    assert canonical["pixel_aspect"] == 1.0
    assert canonical["timecode"] is None


def test_canonical_from_metadata_json_missing_sets_returns_none() -> None:
    """When the expected sets are absent, every canonical key is None."""
    canonical = arri_reader._canonical_from_metadata_json({})
    for key in ("resolution", "framerate", "timecode", "pixel_aspect"):
        assert canonical[key] is None


# ---------- live ART-CMD end-to-end (skipped without fixture) ------------


def _live_art_cmd_or_skip() -> Path:
    art = _arri_art_path()
    if art is None:
        pytest.skip(
            f"set {FORGE_ARRI_ART_PATH_ENV} to a real art-cmd binary to run live e2e tests"
        )
    return art


def _live_clip_or_skip() -> Path:
    """Find one .arx clip directory under tests/fixtures/private/arri/."""
    if not PRIVATE_ARRI_FIXTURES.is_dir():
        pytest.skip(
            f"no private ARRI fixture at {PRIVATE_ARRI_FIXTURES} — drop a real "
            "<clip_name>/<clip_name>.NNNNNNN.arx clip there to enable live tests"
        )
    for child in sorted(PRIVATE_ARRI_FIXTURES.iterdir()):
        if child.is_dir():
            frames = sorted(child.glob("*.arx")) + sorted(child.glob("*.ari"))
            if frames:
                return frames[0]
    pytest.skip(f"no .arx/.ari frames under {PRIVATE_ARRI_FIXTURES}")


def test_live_art_cmd_decodes_one_frame_to_aces_ap0_linear(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end: real art-cmd, real .arx, decoded to ACES AP0 scene-linear EXR."""
    _ = _live_art_cmd_or_skip()
    frame = _live_clip_or_skip()
    # Force the SDK gate closed so we exercise the ART-CMD backend specifically.
    monkeypatch.setattr(arri_reader, "_arri_sdk_available", lambda: False)
    img = read(frame)
    assert img.pixels.dtype == np.float32
    assert img.pixels.ndim == 3 and img.pixels.shape[2] == 3
    assert img.source_colorspace == "ACES2065-1"
    assert img.colorspace == "ACES2065-1"  # no working_space transform
    assert img.bit_depth == 16
    assert np.all(np.isfinite(img.pixels))


def test_live_art_cmd_metadata_only_no_pixel_decode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """read_metadata via ART-CMD export — resolution and framerate extracted."""
    _ = _live_art_cmd_or_skip()
    frame = _live_clip_or_skip()
    monkeypatch.setattr(arri_reader, "_arri_sdk_available", lambda: False)
    meta = read_metadata(frame)
    assert meta.source_colorspace == "ACES2065-1"
    assert "art_cmd_export" in meta.raw_header
    # ARRIRAW always has an Image Size set and Project Rate set in the export.
    assert meta.resolution is not None
    w, h = meta.resolution
    assert w > 0 and h > 0
    assert meta.metadata["framerate"] is not None
    assert meta.metadata["framerate"] > 0
    assert meta.metadata["pixel_aspect"] == 1.0


# ---------- MXF-wrapped ARRIRAW: dispatch + backend selection -------------


def test_get_reader_selects_arri_for_arriraw_mxf(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """When essence classifies as ARRIRAW, `.mxf` dispatches to the ARRI reader."""
    monkeypatch.setattr(ffmpeg_reader, "_classify_mxf", lambda p: ffmpeg_reader._MXF_ARRIRAW)
    path = tmp_path / "B_0006C020.mxf"
    path.write_bytes(b"\x00")
    assert isinstance(get_reader(path), ArriRawReader)


def test_get_reader_selects_ffmpeg_for_editorial_mxf(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Editorial `.mxf` (ProRes/DNxHD) dispatches to the ffmpeg reader, not ARRI."""
    monkeypatch.setattr(ffmpeg_reader, "_classify_mxf", lambda p: ffmpeg_reader._MXF_EDITORIAL)
    path = tmp_path / "editorial.mxf"
    path.write_bytes(b"\x00")
    assert isinstance(get_reader(path), FFmpegReader)


def test_get_reader_unsupported_mxf_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """X-OCN / unclassifiable `.mxf` is claimed by no reader → UnsupportedFileError."""
    monkeypatch.setattr(ffmpeg_reader, "_classify_mxf", lambda p: ffmpeg_reader._MXF_UNSUPPORTED)
    path = tmp_path / "xocn.mxf"
    path.write_bytes(b"\x00")
    with pytest.raises(UnsupportedFileError):
        get_reader(path)


def test_mxf_frame_index_forwarded_to_singlefile_decoder(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """read(mxf, frame_index=N) routes to the single-file MXF decoder with N."""
    monkeypatch.setattr(ffmpeg_reader, "_classify_mxf", lambda p: ffmpeg_reader._MXF_ARRIRAW)
    monkeypatch.setattr(arri_reader, "_arri_sdk_available", lambda: False)
    sentinel = tmp_path / "fake-art"
    monkeypatch.setattr(arri_reader, "_arri_art_path", lambda: sentinel)

    seen: dict[str, object] = {}

    def fake_decode_mxf(art: Path, path: Path, frame_index: int = 0) -> object:
        seen["art"] = art
        seen["frame_index"] = frame_index
        raise RuntimeError("mxf-decode-stub")

    monkeypatch.setattr(arri_reader, "_decode_via_art_cmd_mxf", fake_decode_mxf)
    clip = tmp_path / "clip.mxf"
    clip.write_bytes(b"\x00")
    with pytest.raises(RuntimeError, match="mxf-decode-stub"):
        read(clip, frame_index=42)
    assert seen["art"] == sentinel
    assert seen["frame_index"] == 42


def test_mxf_uses_singlefile_path_not_sequence_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`.mxf` must use the single-file decoder, never the .ari/.arx sequence decoder."""
    monkeypatch.setattr(ffmpeg_reader, "_classify_mxf", lambda p: ffmpeg_reader._MXF_ARRIRAW)
    monkeypatch.setattr(arri_reader, "_arri_sdk_available", lambda: False)
    monkeypatch.setattr(arri_reader, "_arri_art_path", lambda: tmp_path / "fake-art")

    def boom_sequence(*a: object, **k: object) -> object:
        raise AssertionError("sequence decoder must not be called for .mxf")

    def ok_mxf(*a: object, **k: object) -> object:
        raise RuntimeError("ok")

    monkeypatch.setattr(arri_reader, "_decode_via_art_cmd", boom_sequence)
    monkeypatch.setattr(arri_reader, "_decode_via_art_cmd_mxf", ok_mxf)
    clip = tmp_path / "clip.mxf"
    clip.write_bytes(b"\x00")
    with pytest.raises(RuntimeError, match="ok"):
        read(clip)


def test_mxf_negative_frame_index_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="frame_index must be >= 0"):
        arri_reader._decode_via_art_cmd_mxf(tmp_path / "art", tmp_path / "clip.mxf", frame_index=-1)


# ---------- live MXF-wrapped ARRIRAW e2e (skipped without fixture) --------


def _live_arriraw_mxf_or_skip() -> Path:
    if not PRIVATE_ARRI_MXF_FIXTURES.is_dir():
        pytest.skip(
            f"no private ARRIRAW-MXF fixture at {PRIVATE_ARRI_MXF_FIXTURES} — drop or "
            "symlink a real single-file ARRIRAW .mxf there to enable live tests"
        )
    for child in sorted(PRIVATE_ARRI_MXF_FIXTURES.iterdir()):
        if child.is_file() and child.suffix.lower() == ".mxf":
            return child
    pytest.skip(f"no .mxf file under {PRIVATE_ARRI_MXF_FIXTURES}")


def test_live_arriraw_mxf_decodes_one_frame_to_aces_ap0_linear(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end: real art-cmd, real single-file ARRIRAW .mxf → ACES2065-1 EXR."""
    _ = _live_art_cmd_or_skip()
    clip = _live_arriraw_mxf_or_skip()
    monkeypatch.setattr(arri_reader, "_arri_sdk_available", lambda: False)
    # The real file must classify as ARRIRAW so dispatch routes here.
    assert isinstance(get_reader(clip.expanduser().resolve()), ArriRawReader)
    img = read(clip, frame_index=10)
    assert img.pixels.dtype == np.float32
    assert img.pixels.ndim == 3 and img.pixels.shape[2] == 3
    assert img.source_colorspace == "ACES2065-1"
    assert img.bit_depth == 16
    assert np.all(np.isfinite(img.pixels))


def test_live_arriraw_mxf_metadata_only(monkeypatch: pytest.MonkeyPatch) -> None:
    _ = _live_art_cmd_or_skip()
    clip = _live_arriraw_mxf_or_skip()
    monkeypatch.setattr(arri_reader, "_arri_sdk_available", lambda: False)
    meta = read_metadata(clip)
    assert meta.source_colorspace == "ACES2065-1"
    assert "art_cmd_export" in meta.raw_header
    assert meta.resolution is not None
    assert meta.metadata["framerate"] and meta.metadata["framerate"] > 0


