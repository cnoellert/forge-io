"""RED R3D reader scaffold + REDline subprocess backend."""

from __future__ import annotations

import stat
from pathlib import Path

import numpy as np
import pytest

from forge_io import read, read_metadata
from forge_io._registry import get_reader
from forge_io.exceptions import RedSdkUnavailableError
from forge_io.readers import red_reader
from forge_io.readers.red_reader import (
    FORGE_RED_REDLINE_PATH_ENV,
    FORGE_RED_SDK_PATH_ENV,
    RedRawReader,
    _red_redline_available,
    _red_redline_path,
    _red_sdk_available,
)


# Private fixture location for live REDline end-to-end tests. Gitignored.
# Drop or symlink a real .R3D clip under tests/fixtures/private/red/*.R3D and
# the e2e tests will run. Otherwise they skip cleanly.
PRIVATE_RED_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "private" / "red"


# ---------- dispatch + extension handling --------------------------------


def test_get_reader_selects_red_for_r3d(tmp_path: Path) -> None:
    path = tmp_path / "clip.R3D"
    path.write_bytes(b"")
    assert isinstance(get_reader(path), RedRawReader)


def test_get_reader_extension_is_case_insensitive(tmp_path: Path) -> None:
    path = tmp_path / "CLIP.r3d"
    path.write_bytes(b"")
    assert isinstance(get_reader(path), RedRawReader)


# ---------- SDK gate (option 1 backend) ----------------------------------


def test_sdk_unavailable_when_env_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(FORGE_RED_SDK_PATH_ENV, raising=False)
    assert _red_sdk_available() is False


def test_sdk_unavailable_when_env_points_at_missing_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(FORGE_RED_SDK_PATH_ENV, str(tmp_path / "no-such.dylib"))
    assert _red_sdk_available() is False


def test_sdk_unavailable_when_env_points_at_non_library(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    bogus = tmp_path / "not_a_lib.txt"
    bogus.write_text("definitely not a shared object")
    monkeypatch.setenv(FORGE_RED_SDK_PATH_ENV, str(bogus))
    assert _red_sdk_available() is False


def test_sdk_available_with_real_shared_library(monkeypatch: pytest.MonkeyPatch) -> None:
    """ctypes.CDLL succeeds against any real on-disk shared library."""
    import numpy

    np_dir = Path(numpy.__file__).parent
    candidates: list[Path] = []
    for pattern in ("*.so", "*.dylib", "*.pyd"):
        candidates.extend(p for p in np_dir.rglob(pattern) if p.is_file())
    if not candidates:
        pytest.skip("no compiled extension found in the numpy install")
    monkeypatch.setenv(FORGE_RED_SDK_PATH_ENV, str(candidates[0]))
    assert _red_sdk_available() is True


# ---------- REDline gate (option 2 backend) ------------------------------


def test_redline_unavailable_when_env_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(FORGE_RED_REDLINE_PATH_ENV, raising=False)
    assert _red_redline_path() is None
    assert _red_redline_available() is False


def test_redline_unavailable_when_env_points_at_missing_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(FORGE_RED_REDLINE_PATH_ENV, str(tmp_path / "no-such-bin"))
    assert _red_redline_path() is None


def test_redline_unavailable_when_env_points_at_non_executable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    not_exec = tmp_path / "REDline"
    not_exec.write_text("#!/bin/sh\necho hi\n")
    # Deliberately no chmod +x — file is not executable.
    monkeypatch.setenv(FORGE_RED_REDLINE_PATH_ENV, str(not_exec))
    assert _red_redline_path() is None


def test_redline_available_when_env_points_at_executable_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = tmp_path / "REDline"
    fake.write_text("#!/bin/sh\nexit 0\n")
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    monkeypatch.setenv(FORGE_RED_REDLINE_PATH_ENV, str(fake))
    assert _red_redline_path() == fake
    assert _red_redline_available() is True


# ---------- backend selection + error messaging --------------------------


def test_read_r3d_raises_when_both_gates_closed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """No SDK + no REDline → actionable error naming both env vars."""
    monkeypatch.delenv(FORGE_RED_SDK_PATH_ENV, raising=False)
    monkeypatch.delenv(FORGE_RED_REDLINE_PATH_ENV, raising=False)
    path = tmp_path / "clip.r3d"
    path.write_bytes(b"")
    with pytest.raises(RedSdkUnavailableError) as ei:
        read(path)
    msg = str(ei.value)
    assert FORGE_RED_SDK_PATH_ENV in msg
    assert FORGE_RED_REDLINE_PATH_ENV in msg


def test_read_metadata_r3d_raises_when_both_gates_closed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Symmetry with read(): metadata path also names both env vars."""
    monkeypatch.delenv(FORGE_RED_SDK_PATH_ENV, raising=False)
    monkeypatch.delenv(FORGE_RED_REDLINE_PATH_ENV, raising=False)
    path = tmp_path / "clip.r3d"
    path.write_bytes(b"")
    with pytest.raises(RedSdkUnavailableError) as ei:
        read_metadata(path)
    assert FORGE_RED_REDLINE_PATH_ENV in str(ei.value)


def test_sdk_gate_open_raises_not_implemented(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """SDK gate takes precedence over REDline; sibling package not yet implemented."""
    monkeypatch.setattr(red_reader, "_red_sdk_available", lambda: True)
    monkeypatch.setattr(red_reader, "_red_redline_path", lambda: None)
    path = tmp_path / "clip.r3d"
    path.write_bytes(b"")
    with pytest.raises(NotImplementedError):
        read(path)
    with pytest.raises(NotImplementedError):
        read_metadata(path)


def test_redline_backend_invoked_when_sdk_closed_and_redline_open(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """When only REDline is configured, read() dispatches to the REDline backend."""
    monkeypatch.setattr(red_reader, "_red_sdk_available", lambda: False)
    sentinel = tmp_path / "fake-REDline"
    monkeypatch.setattr(red_reader, "_red_redline_path", lambda: sentinel)

    called: dict[str, object] = {}

    def fake_decode(redline: Path, path: Path, frame_index: int = 0) -> object:
        called["redline"] = redline
        called["path"] = path
        called["frame_index"] = frame_index
        raise RuntimeError("decode-stub-reached")

    monkeypatch.setattr(red_reader, "_decode_via_redline", fake_decode)
    clip = tmp_path / "clip.r3d"
    clip.write_bytes(b"")
    with pytest.raises(RuntimeError, match="decode-stub-reached"):
        read(clip)
    assert called["redline"] == sentinel
    assert Path(str(called["path"])).name == "clip.r3d"
    assert called["frame_index"] == 0


def test_red_frame_index_forwarded_to_decoder(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """forge_io.read(frame_index=N) forwards N to the REDline backend."""
    monkeypatch.setattr(red_reader, "_red_sdk_available", lambda: False)
    sentinel = tmp_path / "fake-REDline"
    monkeypatch.setattr(red_reader, "_red_redline_path", lambda: sentinel)

    seen: dict[str, object] = {}

    def fake_decode(redline: Path, path: Path, frame_index: int = 0) -> object:
        seen["frame_index"] = frame_index
        raise RuntimeError("stub")

    monkeypatch.setattr(red_reader, "_decode_via_redline", fake_decode)
    clip = tmp_path / "clip.r3d"
    clip.write_bytes(b"")
    with pytest.raises(RuntimeError, match="stub"):
        read(clip, frame_index=42)
    assert seen["frame_index"] == 42


# ---------- REDline parser + canonical builder (unit) --------------------


def test_parse_redline_keyvalue_normal_form() -> None:
    """REDline --printMeta 1 emits ``Key:\\tValue`` per line."""
    sample = (
        "Clip Name:\tA005_W055_0819CM_001.R3D\n"
        "Frame Width:\t8192\n"
        "Frame Height:\t4320\n"
        "FPS:\t23.976024627685547\n"
        "Pixel Aspect Ratio:\t1\n"
        "Abs TC:\t15:43:20:20\n"
        "Edge TC:\t03:33:58:06\n"
        "Camera Network Name:\t\n"
        "Clip Current Image Pipeline:\tIPP2\n"
    )
    fields = red_reader._parse_redline_keyvalue(sample)
    assert fields["Clip Name"] == "A005_W055_0819CM_001.R3D"
    assert fields["Frame Width"] == "8192"
    assert fields["FPS"] == "23.976024627685547"
    assert fields["Abs TC"] == "15:43:20:20"
    assert fields["Camera Network Name"] == ""  # empty value preserved
    assert fields["Clip Current Image Pipeline"] == "IPP2"


def test_parse_redline_keyvalue_ignores_lines_without_colon() -> None:
    """Stray banner / blank lines must not poison the dict."""
    sample = (
        "Some banner with no colon\n"
        "\n"
        "Clip Name:\tFoo\n"
    )
    fields = red_reader._parse_redline_keyvalue(sample)
    assert fields == {"Clip Name": "Foo"}


def test_canonical_from_redline_fields_synthetic() -> None:
    """Synthetic field map — runs without invoking REDline."""
    fields = {
        "Frame Width": "8192",
        "Frame Height": "4320",
        "FPS": "23.976024627685547",
        "Pixel Aspect Ratio": "1",
        "Abs TC": "15:43:20:20",
        "Clip Current Image Pipeline": "IPP2",
    }
    canonical, resolution = red_reader._canonical_from_redline_fields(fields)
    assert resolution == (8192, 4320)
    assert canonical["resolution"] == (8192, 4320)
    assert canonical["framerate"] == pytest.approx(23.976024627685547)
    assert canonical["pixel_aspect"] == 1.0
    assert canonical["timecode"] == "15:43:20:20"


def test_canonical_from_redline_fields_missing_returns_none() -> None:
    """When fields are absent, every canonical key is None and resolution is None."""
    canonical, resolution = red_reader._canonical_from_redline_fields({})
    assert resolution is None
    for key in ("resolution", "framerate", "pixel_aspect", "timecode"):
        assert canonical[key] is None


def test_canonical_from_exr_attribs_prefers_rational_framerate() -> None:
    """EXR-standard ``framesPerSecond = (num, denom)`` beats the float ``FPS``."""
    raw = {
        "framesPerSecond": (24000, 1001),
        "FPS": 23.976024627685547,
        "PixelAspectRatio": 1.0,
        "TOD TC Start": "15:43:20:20",
        "Edgecode Start": "03:33:58:06",  # not the canonical TC
    }
    canonical = red_reader._canonical_from_exr_attribs(raw, (8192, 4320))
    assert canonical["resolution"] == (8192, 4320)
    # 24000/1001 is the exact NTSC rational; the float FPS is the lossy form.
    assert canonical["framerate"] == pytest.approx(24000 / 1001)
    assert canonical["pixel_aspect"] == 1.0
    assert canonical["timecode"] == "15:43:20:20"


def test_canonical_from_exr_attribs_falls_back_to_float_fps() -> None:
    raw: dict = {
        "FPS": 23.976,
        "PixelAspectRatio": 1.0,
        "TOD TC Start": "00:00:00:00",
    }
    canonical = red_reader._canonical_from_exr_attribs(raw, (1920, 1080))
    assert canonical["framerate"] == pytest.approx(23.976)


def test_canonical_from_exr_attribs_handles_missing_fields() -> None:
    canonical = red_reader._canonical_from_exr_attribs({}, (1920, 1080))
    assert canonical["resolution"] == (1920, 1080)
    assert canonical["framerate"] is None
    assert canonical["pixel_aspect"] is None
    assert canonical["timecode"] is None


# ---------- live REDline end-to-end (skipped without fixture) ------------


def _live_redline_or_skip() -> Path:
    redline = _red_redline_path()
    if redline is None:
        pytest.skip(
            f"set {FORGE_RED_REDLINE_PATH_ENV} to a real REDline binary "
            "(REDCINE-X PRO ships it) to run live e2e tests"
        )
    return redline


def _live_clip_or_skip() -> Path:
    """Find one ``.R3D`` clip under tests/fixtures/private/red/."""
    if not PRIVATE_RED_FIXTURES.is_dir():
        pytest.skip(
            f"no private RED fixture at {PRIVATE_RED_FIXTURES} — drop a real "
            ".R3D clip there (or symlink) to enable live tests"
        )
    for child in sorted(PRIVATE_RED_FIXTURES.iterdir()):
        if child.is_file() and child.suffix.lower() == ".r3d":
            return child
    pytest.skip(f"no .R3D file under {PRIVATE_RED_FIXTURES}")


def test_live_redline_decodes_one_frame_to_rwg_linear(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end: real REDline, real .R3D, decoded to REDWideGamutRGB linear EXR."""
    _ = _live_redline_or_skip()
    clip = _live_clip_or_skip()
    # Force the SDK gate closed so we exercise the REDline backend specifically.
    monkeypatch.setattr(red_reader, "_red_sdk_available", lambda: False)
    img = read(clip)
    assert img.pixels.dtype == np.float32
    assert img.pixels.ndim == 3 and img.pixels.shape[2] == 3
    assert img.source_colorspace == "Linear REDWideGamutRGB"
    assert img.colorspace == "Linear REDWideGamutRGB"  # no working_space transform
    assert img.bit_depth == 16
    assert np.all(np.isfinite(img.pixels))
    # Scene-linear: max can well exceed 1.0 (specular highlights).
    assert img.pixels.max() >= 0.0


def test_live_redline_metadata_only_no_pixel_decode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """read_metadata via REDline --printMeta — resolution and framerate extracted."""
    _ = _live_redline_or_skip()
    clip = _live_clip_or_skip()
    monkeypatch.setattr(red_reader, "_red_sdk_available", lambda: False)
    meta = read_metadata(clip)
    assert meta.source_colorspace == "Linear REDWideGamutRGB"
    assert "redline_meta" in meta.raw_header
    assert meta.resolution is not None
    w, h = meta.resolution
    assert w > 0 and h > 0
    assert meta.metadata["framerate"] is not None
    assert meta.metadata["framerate"] > 0
    assert meta.metadata["pixel_aspect"] == 1.0
