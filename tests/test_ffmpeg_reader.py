"""FFmpeg container reader: dispatch, availability gate, parsers, and live e2e."""

from __future__ import annotations

import stat
import subprocess
from pathlib import Path

import numpy as np
import pytest

from forge_io import read, read_metadata
from forge_io._registry import get_reader
from forge_io.exceptions import FFmpegUnavailableError
from forge_io.readers import ffmpeg_reader
from forge_io.readers.ffmpeg_reader import (
    _MXF_ARRIRAW,
    _MXF_EDITORIAL,
    _MXF_UNSUPPORTED,
    FORGE_FFMPEG_PATH_ENV,
    FORGE_FFPROBE_PATH_ENV,
    FFmpegReader,
    _classify_mxf_doc,
    _ffmpeg_available,
    _ffmpeg_path,
    _ffprobe_path,
)

# ---------- dispatch + extension handling --------------------------------


@pytest.mark.parametrize("name", ["clip.mov", "clip.mp4", "clip.m4v", "clip.avi", "clip.mkv"])
def test_get_reader_selects_ffmpeg_for_containers(tmp_path: Path, name: str) -> None:
    path = tmp_path / name
    path.write_bytes(b"")
    assert isinstance(get_reader(path), FFmpegReader)


def test_get_reader_extension_is_case_insensitive(tmp_path: Path) -> None:
    path = tmp_path / "CLIP.MOV"
    path.write_bytes(b"")
    assert isinstance(get_reader(path), FFmpegReader)


def test_mxf_is_not_claimed_by_ffmpeg_reader(tmp_path: Path) -> None:
    """.mxf stays with the Sony policy (unregistered) — not the ffmpeg reader."""
    path = tmp_path / "clip.mxf"
    path.write_bytes(b"")
    assert FFmpegReader().can_read(path) is False


# ---------- binary discovery gate ----------------------------------------


def test_unavailable_when_env_points_at_missing_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(FORGE_FFMPEG_PATH_ENV, str(tmp_path / "no-such-ffmpeg"))
    assert _ffmpeg_path() is None


def test_unavailable_when_env_points_at_non_executable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    not_exec = tmp_path / "ffmpeg"
    not_exec.write_text("#!/bin/sh\nexit 0\n")  # deliberately not chmod +x
    monkeypatch.setenv(FORGE_FFMPEG_PATH_ENV, str(not_exec))
    assert _ffmpeg_path() is None


def test_env_override_resolves_executable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = tmp_path / "ffmpeg"
    fake.write_text("#!/bin/sh\nexit 0\n")
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    monkeypatch.setenv(FORGE_FFMPEG_PATH_ENV, str(fake))
    assert _ffmpeg_path() == fake


def test_falls_back_to_path_when_env_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(FORGE_FFMPEG_PATH_ENV, raising=False)
    monkeypatch.setattr(ffmpeg_reader.shutil, "which", lambda name: "/usr/bin/" + name)
    assert _ffmpeg_path() == Path("/usr/bin/ffmpeg")


def test_unavailable_when_not_on_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(FORGE_FFMPEG_PATH_ENV, raising=False)
    monkeypatch.delenv(FORGE_FFPROBE_PATH_ENV, raising=False)
    monkeypatch.setattr(ffmpeg_reader.shutil, "which", lambda name: None)
    assert _ffmpeg_path() is None
    assert _ffprobe_path() is None
    assert _ffmpeg_available() is False


# ---------- backend gate + error messaging -------------------------------


def test_read_raises_when_binaries_unavailable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(ffmpeg_reader, "_ffmpeg_path", lambda: None)
    monkeypatch.setattr(ffmpeg_reader, "_ffprobe_path", lambda: None)
    clip = tmp_path / "clip.mov"
    clip.write_bytes(b"\x00")
    with pytest.raises(FFmpegUnavailableError) as ei:
        read(clip)
    msg = str(ei.value)
    assert FORGE_FFMPEG_PATH_ENV in msg
    assert FORGE_FFPROBE_PATH_ENV in msg


def test_read_metadata_raises_when_ffprobe_unavailable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(ffmpeg_reader, "_ffprobe_path", lambda: None)
    clip = tmp_path / "clip.mov"
    clip.write_bytes(b"\x00")
    with pytest.raises(FFmpegUnavailableError):
        read_metadata(clip)


def test_frame_index_forwarded_to_decoder(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """forge_io.read(frame_index=N) forwards N to the ffmpeg backend."""
    monkeypatch.setattr(ffmpeg_reader, "_ffmpeg_path", lambda: tmp_path / "ffmpeg")
    monkeypatch.setattr(ffmpeg_reader, "_ffprobe_path", lambda: tmp_path / "ffprobe")

    seen: dict[str, object] = {}

    def fake_decode(ffmpeg: Path, ffprobe: Path, path: Path, frame_index: int) -> object:
        seen["frame_index"] = frame_index
        raise RuntimeError("decode-stub")

    monkeypatch.setattr(ffmpeg_reader, "_decode_via_ffmpeg", fake_decode)
    clip = tmp_path / "clip.mov"
    clip.write_bytes(b"\x00")
    with pytest.raises(RuntimeError, match="decode-stub"):
        read(clip, frame_index=37)
    assert seen["frame_index"] == 37


def test_negative_frame_index_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="frame_index must be >= 0"):
        ffmpeg_reader._decode_frame(tmp_path / "ffmpeg", tmp_path / "x.mov", -1, tmp_path / "o.png")


# ---------- ffprobe parsers (unit, no subprocess) ------------------------


def test_parse_ratio_fps_and_aspect() -> None:
    assert ffmpeg_reader._parse_ratio("24000/1001") == pytest.approx(24000 / 1001)
    assert ffmpeg_reader._parse_ratio("1:1") == 1.0
    assert ffmpeg_reader._parse_ratio("0/0") is None
    assert ffmpeg_reader._parse_ratio("N/A") is None
    assert ffmpeg_reader._parse_ratio(None) is None


def test_stream_bit_depth_prefers_bits_per_raw_sample() -> None:
    assert ffmpeg_reader._stream_bit_depth({"bits_per_raw_sample": "10"}) == 10
    assert ffmpeg_reader._stream_bit_depth({"pix_fmt": "yuv422p10le"}) == 10
    assert ffmpeg_reader._stream_bit_depth({"pix_fmt": "yuv444p12le"}) == 12
    assert ffmpeg_reader._stream_bit_depth({"pix_fmt": "yuv420p"}) == 8
    assert ffmpeg_reader._stream_bit_depth({}) == 8


def test_frame_count_from_nb_frames_then_duration() -> None:
    assert ffmpeg_reader._frame_count({"nb_frames": "48"}, 24.0, {}) == 48
    # Fallback: duration × fps, rounded.
    assert ffmpeg_reader._frame_count({"duration": "2.0"}, 24.0, {}) == 48
    assert ffmpeg_reader._frame_count({}, None, {}) is None


def test_timecode_prefers_stream_then_format() -> None:
    doc = {"format": {"tags": {"timecode": "01:00:00:00"}}}
    assert ffmpeg_reader._timecode({"tags": {"timecode": "10:00:00:00"}}, doc) == "10:00:00:00"
    assert ffmpeg_reader._timecode({}, doc) == "01:00:00:00"
    assert ffmpeg_reader._timecode({}, {}) is None


def test_canonical_and_raw_from_synthetic_doc() -> None:
    doc = {
        "streams": [
            {"codec_type": "audio"},
            {
                "codec_type": "video",
                "codec_name": "prores",
                "width": 1920,
                "height": 1080,
                "pix_fmt": "yuv422p10le",
                "bits_per_raw_sample": "10",
                "r_frame_rate": "24000/1001",
                "sample_aspect_ratio": "1:1",
                "nb_frames": "240",
                "color_primaries": "bt709",
                "color_transfer": "bt709",
                "tags": {"timecode": "01:00:00:00"},
            },
        ],
        "format": {"duration": "10.0"},
    }
    canonical, resolution, bit_depth, raw = ffmpeg_reader._canonical_and_raw(doc, Path("x.mov"))
    assert resolution == (1920, 1080)
    assert bit_depth == 10
    assert canonical["resolution"] == (1920, 1080)
    assert canonical["framerate"] == pytest.approx(24000 / 1001)
    assert canonical["pixel_aspect"] == 1.0
    assert canonical["timecode"] == "01:00:00:00"
    assert raw["frame_count"] == 240
    assert raw["codec_name"] == "prores"
    assert raw["color_primaries"] == "bt709"


def test_canonical_and_raw_raises_without_video_stream() -> None:
    from forge_io.exceptions import ImageDecodeError

    with pytest.raises(ImageDecodeError, match="no video stream"):
        ffmpeg_reader._canonical_and_raw({"streams": [{"codec_type": "audio"}]}, Path("x.mov"))


# ---------- MXF essence classification (unit, no subprocess) --------------


def test_classify_mxf_doc_editorial_known_codec() -> None:
    for codec in ("prores", "dnxhd", "mpeg2video"):
        doc = {
            "streams": [
                {"codec_type": "video", "codec_name": codec, "width": 1920, "height": 1080}
            ]
        }
        assert _classify_mxf_doc(doc) == _MXF_EDITORIAL


def test_classify_mxf_doc_arriraw_by_company_tag() -> None:
    # ARRIRAW ffprobe signature: unknown codec, 0x0 dims, but ARRI company tag.
    doc = {
        "streams": [{"codec_type": "video", "codec_name": "unknown", "width": 0, "height": 0}],
        "format": {"tags": {"company_name": "ARRI", "product_name": "ALEXA 35"}},
    }
    assert _classify_mxf_doc(doc) == _MXF_ARRIRAW


def test_classify_mxf_doc_editorial_wins_over_arri_company() -> None:
    """An ARRI clip in ProRes (known codec) is editorial (ffmpeg), not ARRIRAW."""
    doc = {
        "streams": [{"codec_type": "video", "codec_name": "prores", "width": 4608, "height": 3164}],
        "format": {"tags": {"company_name": "ARRI"}},
    }
    assert _classify_mxf_doc(doc) == _MXF_EDITORIAL


def test_classify_mxf_doc_sony_xocn_unsupported() -> None:
    doc = {
        "streams": [{"codec_type": "video", "codec_name": "unknown", "width": 0, "height": 0}],
        "format": {"tags": {"company_name": "Sony", "product_name": "VENICE"}},
    }
    assert _classify_mxf_doc(doc) == _MXF_UNSUPPORTED


def test_classify_mxf_doc_empty_and_audio_only_unsupported() -> None:
    assert _classify_mxf_doc({}) == _MXF_UNSUPPORTED
    assert _classify_mxf_doc({"streams": [{"codec_type": "audio"}]}) == _MXF_UNSUPPORTED
    # Zero-dim video with no company tag (corrupt) is unsupported, not editorial.
    doc = {"streams": [{"codec_type": "video", "codec_name": "unknown", "width": 0, "height": 0}]}
    assert _classify_mxf_doc(doc) == _MXF_UNSUPPORTED


# ---------- live ffmpeg end-to-end (skipped without ffmpeg) --------------


def _ffmpeg_or_skip() -> tuple[Path, Path]:
    ffmpeg, ffprobe = _ffmpeg_path(), _ffprobe_path()
    if ffmpeg is None or ffprobe is None:
        pytest.skip("ffmpeg/ffprobe not available (install ffmpeg to run live container tests)")
    return ffmpeg, ffprobe


# Per-frame solid colors so frame accuracy is verifiable from the decoded pixel.
_FRAME_COUNT = 8


def _frame_color(n: int) -> tuple[int, int, int]:
    return ((n + 1) * 20, 50, 100)  # R encodes the frame index


def _make_lossless_container(ffmpeg: Path, tmp_path: Path, suffix: str, codec: str) -> Path:
    """Build a container of _FRAME_COUNT distinct solid-color frames via a codec."""
    import OpenImageIO as oiio

    frames_dir = tmp_path / "frames"
    frames_dir.mkdir()
    w = h = 16
    for n in range(_FRAME_COUNT):
        r, g, b = (c / 255.0 for c in _frame_color(n))
        spec = oiio.ImageSpec(w, h, 3, oiio.UINT8)
        buf = oiio.ImageBuf(spec)
        oiio.ImageBufAlgo.fill(buf, (r, g, b))
        assert buf.write(str(frames_dir / f"frame_{n:03d}.png"))

    out = tmp_path / f"clip{suffix}"
    subprocess.run(
        [
            str(ffmpeg), "-y", "-nostdin",
            "-framerate", "24",
            "-start_number", "0",
            "-i", str(frames_dir / "frame_%03d.png"),
            "-c:v", codec,
            "-pix_fmt", "rgb24" if codec == "ffv1" else "yuv420p",
            str(out),
        ],
        check=True,
        capture_output=True,
    )
    return out


def test_live_ffv1_frame_accurate_decode(tmp_path: Path) -> None:
    """Lossless RGB round-trip: each frame_index decodes to its exact solid color."""
    ffmpeg, _ffprobe = _ffmpeg_or_skip()
    clip = _make_lossless_container(ffmpeg, tmp_path, ".mkv", "ffv1")
    for n in range(_FRAME_COUNT):
        img = read(clip, frame_index=n)
        assert img.pixels.dtype == np.float32
        assert img.pixels.ndim == 3 and img.pixels.shape[2] == 3
        assert img.source_colorspace == "unknown"
        expected_r = _frame_color(n)[0] / 255.0
        assert img.pixels[..., 0].mean() == pytest.approx(expected_r, abs=2 / 255)


def test_live_metadata_only_no_pixel_decode(tmp_path: Path) -> None:
    ffmpeg, _ffprobe = _ffmpeg_or_skip()
    clip = _make_lossless_container(ffmpeg, tmp_path, ".mkv", "ffv1")
    meta = read_metadata(clip)
    assert meta.source_colorspace == "unknown"
    assert meta.resolution == (16, 16)
    assert meta.metadata["framerate"] == pytest.approx(24.0)
    assert meta.raw_header["frame_count"] == _FRAME_COUNT
    assert "ffprobe" in meta.raw_header


def test_live_h264_long_gop_frame_accuracy(tmp_path: Path) -> None:
    """Long-GOP decode-from-head lands on the requested frame, not a keyframe neighbour."""
    ffmpeg, _ffprobe = _ffmpeg_or_skip()
    try:
        clip = _make_lossless_container(ffmpeg, tmp_path, ".mp4", "libx264")
    except subprocess.CalledProcessError:
        pytest.skip("this ffmpeg build lacks libx264")
    for n in range(_FRAME_COUNT):
        img = read(clip, frame_index=n)
        decoded_r = img.pixels[..., 0].mean() * 255.0
        # The nearest original frame color to the decoded frame must be frame n.
        nearest = min(range(_FRAME_COUNT), key=lambda k: abs(_frame_color(k)[0] - decoded_r))
        assert nearest == n, f"frame_index={n} decoded closest to frame {nearest}"


def _make_editorial_mxf(ffmpeg: Path, tmp_path: Path) -> Path:
    out = tmp_path / "editorial.mxf"
    subprocess.run(
        [
            str(ffmpeg), "-y", "-nostdin", "-loglevel", "error",
            "-f", "lavfi", "-i", "testsrc=size=320x240:rate=24:duration=1",
            "-c:v", "prores_ks", "-profile:v", "3", "-f", "mxf", str(out),
        ],
        check=True,
        capture_output=True,
    )
    return out


def test_live_editorial_mxf_dispatches_and_decodes(tmp_path: Path) -> None:
    """A ProRes-in-MXF is classified editorial, routed to FFmpegReader, and decodes."""
    ffmpeg, _ffprobe = _ffmpeg_or_skip()
    clip = _make_editorial_mxf(ffmpeg, tmp_path)
    # Real ffprobe classification drives dispatch here (no monkeypatch).
    assert isinstance(get_reader(clip.resolve()), FFmpegReader)
    img = read(clip, frame_index=3)
    assert img.pixels.dtype == np.float32
    assert img.pixels.ndim == 3 and img.pixels.shape[2] == 3
    assert img.source_colorspace == "unknown"
    meta = read_metadata(clip)
    assert meta.resolution == (320, 240)
    assert meta.source_colorspace == "unknown"
