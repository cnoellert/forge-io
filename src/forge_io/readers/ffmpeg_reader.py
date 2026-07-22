"""Editorial/delivery container reader (``.mov`` / ``.mp4`` / ``.mkv`` …) via ffmpeg.

Common editorial and delivery containers — QuickTime ProRes / H.264 ``.mov``,
``.mp4``, etc. — carry gamma-encoded video, not camera raw. forge-io's vendor
readers (ARRI ART-CMD, RED REDline) cover camera-raw containers; this reader
covers everything else that ffmpeg can demux and decode.

Backend
-------
A single **ffmpeg subprocess** decodes one frame to a 16-bit RGB PNG in a temp
dir; forge-io reads it back via OIIO and returns ``float32 (H, W, 3)``.
**ffprobe** supplies header metadata (resolution, framerate, pixel aspect,
timecode, frame count) without decoding pixels. forge-io does **not** bundle
ffmpeg — both binaries are discovered on ``PATH`` (or via
``FORGE_FFMPEG_PATH`` / ``FORGE_FFPROBE_PATH``); if neither is found,
``FFmpegUnavailableError`` names the options.

Frame-accurate seeking
----------------------
``read_pixels`` selects the requested 0-based frame by **frame number**
(``-vf select='gte(n\\,N)' -frames:v 1``), which decodes from the head of the
stream. This is exact for long-GOP codecs (H.264/H.265) where input-side
time seeking (``-ss`` before ``-i``) can land on the wrong frame after a
keyframe. The trade-off is that decoding a high frame index walks the stream
from the start — correctness over speed, matching the issue's requirement.

Colorspace posture
------------------
Editorial containers are reported as ``source_colorspace="unknown"`` — the same
posture ``OIIOReader`` takes for PNG/JPEG/DPX. forge-io does not infer a
colorspace from container tags or filenames; callers apply ``working_space``
with an explicit ``assume_source`` (e.g. the host segment's colorspace). The
raw ffprobe color tags (``color_primaries`` / ``color_transfer`` /
``color_space`` / ``color_range``) are preserved in ``raw_header`` for callers
that want to make their own decision.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from forge_io._types import UNKNOWN_COLORSPACE, ImageMetadata, merge_canonical
from forge_io.exceptions import FFmpegUnavailableError, ImageDecodeError
from forge_io.readers._base import Reader, ReaderDecode

# Editorial/delivery containers only. Camera-raw containers (.ari/.arx/.r3d)
# have dedicated vendor readers; .mxf is intentionally excluded — it cannot be
# disambiguated at the extension level between editorial MXF (DNxHD/XDCAM,
# decodable) and Sony X-OCN raw (not ffmpeg-decodable), and forge-io's Sony
# policy keeps .mxf unregistered (see README §7).
_FFMPEG_EXTENSIONS = frozenset({".mov", ".mp4", ".m4v", ".avi", ".mkv"})

FORGE_FFMPEG_PATH_ENV = "FORGE_FFMPEG_PATH"
FORGE_FFPROBE_PATH_ENV = "FORGE_FFPROBE_PATH"

# 16-bit big-endian RGB PNG: lossless, OIIO-readable, preserves 10/12-bit
# ProRes precision. ffmpeg's swscale honours the stream's tagged range when
# expanding YUV → full-range RGB.
_FFMPEG_PIX_FMT = "rgb48be"

# pix_fmt name fragments → bits per component, used only when ffprobe does not
# report ``bits_per_raw_sample`` for the stream.
_PIX_FMT_BITS = (
    ("12", 12),
    ("10", 10),
    ("16", 16),
)

_NO_BACKEND_MSG = (
    "Editorial/delivery container decode requires ffmpeg + ffprobe, which\n"
    "forge-io does not bundle. Install ffmpeg so both binaries are on PATH\n"
    f"(Homebrew: `brew install ffmpeg`; conda-forge: `conda install ffmpeg`),\n"
    f"or point {FORGE_FFMPEG_PATH_ENV} / {FORGE_FFPROBE_PATH_ENV} at them.\n"
    "Neither is available in this environment."
)


def _resolve_binary(env_var: str, name: str) -> Path | None:
    """Resolve a binary from ``env_var`` (if set + executable) else ``PATH``."""
    env_path = os.environ.get(env_var)
    if env_path:
        p = Path(env_path).expanduser()
        if p.is_file() and os.access(p, os.X_OK):
            return p
        return None
    found = shutil.which(name)
    return Path(found) if found else None


def _ffmpeg_path() -> Path | None:
    return _resolve_binary(FORGE_FFMPEG_PATH_ENV, "ffmpeg")


def _ffprobe_path() -> Path | None:
    return _resolve_binary(FORGE_FFPROBE_PATH_ENV, "ffprobe")


def _ffmpeg_available() -> bool:
    """Both binaries must be resolvable — decode needs ffmpeg, metadata ffprobe."""
    return _ffmpeg_path() is not None and _ffprobe_path() is not None


def _run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    """Run a subprocess with stderr captured for error reporting."""
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd),
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as e:
        raise ImageDecodeError(cwd, f"binary not found: {cmd[0]}") from e
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-5:]
        raise ImageDecodeError(
            cwd,
            f"{Path(cmd[0]).name} failed (exit {proc.returncode}): "
            f"{' | '.join(tail) or 'no stderr'}",
        )
    return proc


def _probe(ffprobe: Path, path: Path) -> dict[str, Any]:
    """Run ffprobe and return the parsed JSON document (streams + format)."""
    proc = _run(
        [
            str(ffprobe),
            "-v",
            "quiet",
            "-print_format",
            "json",
            "-show_streams",
            "-show_format",
            str(path),
        ],
        cwd=path.parent,
    )
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        raise ImageDecodeError(path, f"malformed ffprobe JSON: {e}") from e


def _video_stream(doc: dict[str, Any], path: Path) -> dict[str, Any]:
    """Return the first video stream from an ffprobe document."""
    for s in doc.get("streams") or []:
        if isinstance(s, dict) and s.get("codec_type") == "video":
            return s
    raise ImageDecodeError(path, "no video stream found in container")


def _parse_ratio(s: Any) -> float | None:
    """Parse ffprobe ratios: ``"24000/1001"`` (fps) or ``"1:1"`` (aspect)."""
    if not isinstance(s, str):
        return None
    sep = "/" if "/" in s else (":" if ":" in s else None)
    if sep is None:
        return None
    parts = s.split(sep)
    if len(parts) != 2:
        return None
    try:
        num, den = int(parts[0]), int(parts[1])
    except ValueError:
        return None
    if den == 0:
        return None
    return num / den


def _stream_bit_depth(vstream: dict[str, Any]) -> int:
    """Source bits per component: ffprobe ``bits_per_raw_sample`` else pix_fmt else 8."""
    raw = vstream.get("bits_per_raw_sample")
    try:
        if raw is not None and int(raw) > 0:
            return int(raw)
    except (TypeError, ValueError):
        pass
    pix_fmt = str(vstream.get("pix_fmt") or "")
    for fragment, bits in _PIX_FMT_BITS:
        if fragment in pix_fmt:
            return bits
    return 8


def _frame_count(
    vstream: dict[str, Any], framerate: float | None, doc: dict[str, Any]
) -> int | None:
    """Frame count: ffprobe ``nb_frames`` else derived from duration × fps."""
    nb = vstream.get("nb_frames")
    try:
        if nb is not None and int(nb) > 0:
            return int(nb)
    except (TypeError, ValueError):
        pass
    duration = vstream.get("duration") or (doc.get("format") or {}).get("duration")
    try:
        if duration is not None and framerate:
            return int(round(float(duration) * framerate))
    except (TypeError, ValueError):
        pass
    return None


def _timecode(vstream: dict[str, Any], doc: dict[str, Any]) -> str | None:
    """Prefer the stream timecode tag, fall back to the container/format tag."""
    for tags in (vstream.get("tags") or {}, (doc.get("format") or {}).get("tags") or {}):
        tc = tags.get("timecode")
        if isinstance(tc, str) and tc:
            return tc
    return None


def _canonical_and_raw(
    doc: dict[str, Any], path: Path
) -> tuple[dict[str, Any], tuple[int, int], int, dict[str, Any]]:
    """Build canonical metadata + resolution + bit depth + raw header from ffprobe."""
    vstream = _video_stream(doc, path)
    width, height = vstream.get("width"), vstream.get("height")
    if not isinstance(width, int) or not isinstance(height, int):
        raise ImageDecodeError(path, "ffprobe reported no integer video dimensions")
    resolution = (width, height)

    # r_frame_rate is the constant frame rate; avg_frame_rate is the fallback
    # for variable/oddly-tagged streams.
    framerate = _parse_ratio(vstream.get("r_frame_rate")) or _parse_ratio(
        vstream.get("avg_frame_rate")
    )
    pixel_aspect = _parse_ratio(vstream.get("sample_aspect_ratio"))
    timecode = _timecode(vstream, doc)
    bit_depth = _stream_bit_depth(vstream)
    frame_count = _frame_count(vstream, framerate, doc)

    canonical = merge_canonical(
        None,
        resolution=resolution,
        pixel_aspect=pixel_aspect,
        timecode=timecode,
        framerate=framerate,
    )
    raw: dict[str, Any] = {
        "ffprobe": doc,
        "frame_count": frame_count,
        "fps": framerate,
        "codec_name": vstream.get("codec_name"),
        "pix_fmt": vstream.get("pix_fmt"),
        "color_primaries": vstream.get("color_primaries"),
        "color_transfer": vstream.get("color_transfer"),
        "color_space": vstream.get("color_space"),
        "color_range": vstream.get("color_range"),
    }
    return canonical, resolution, bit_depth, raw


def _decode_frame(ffmpeg: Path, path: Path, frame_index: int, out_png: Path) -> None:
    """Decode-from-head to ``out_png`` the first frame with ``n >= frame_index``."""
    if frame_index < 0:
        raise ValueError(f"frame_index must be >= 0, got {frame_index}")
    _run(
        [
            str(ffmpeg),
            "-nostdin",
            "-y",
            "-i",
            str(path),
            "-vf",
            f"select=gte(n\\,{frame_index})",
            "-frames:v",
            "1",
            "-fps_mode",
            "passthrough",
            "-pix_fmt",
            _FFMPEG_PIX_FMT,
            str(out_png),
        ],
        cwd=out_png.parent,
    )


def _read_decoded_png(png_path: Path) -> tuple[Any, tuple[int, int]]:
    """Read the PNG ffmpeg wrote via OIIO, returning ``(pixels float32 HxWx3, (w,h))``."""
    import numpy as np
    import OpenImageIO as oiio

    buf = oiio.ImageBuf(str(png_path), 0, 0)
    if buf.has_error:
        raise ImageDecodeError(png_path, buf.geterror() or "ImageBuf error on ffmpeg output")
    spec = buf.spec()
    if spec.nchannels < 3:
        raise ImageDecodeError(png_path, f"ffmpeg PNG has {spec.nchannels} channels, need 3")
    out = oiio.ImageBuf()
    ch0, ch1, ch2 = spec.channelnames[:3]
    if not oiio.ImageBufAlgo.channels(out, buf, (ch0, ch1, ch2), ("R", "G", "B")):
        raise ImageDecodeError(png_path, oiio.geterror() or "channel select failed")
    pixels = out.get_pixels(oiio.FLOAT)
    arr = np.asarray(pixels, dtype=np.float32)
    if arr.ndim == 2:
        arr = arr[..., np.newaxis]
    h, w, _ = arr.shape
    return arr, (w, h)


def _decode_via_ffmpeg(ffmpeg: Path, ffprobe: Path, path: Path, frame_index: int) -> ReaderDecode:
    """Decode one frame of a container to ``float32`` RGB, with ffprobe metadata."""
    doc = _probe(ffprobe, path)
    canonical, _res_probe, bit_depth, raw = _canonical_and_raw(doc, path)
    with tempfile.TemporaryDirectory(prefix="forge-io-ffmpeg-") as tmp:
        out_png = Path(tmp) / "frame.png"
        _decode_frame(ffmpeg, path, frame_index, out_png)
        if not out_png.is_file():
            raise ImageDecodeError(path, "ffmpeg produced no output frame")
        pixels, resolution = _read_decoded_png(out_png)
    # Resolution from the decoded pixels is authoritative (it reflects any
    # SAR handling ffmpeg applied); keep canonical consistent with it.
    canonical = merge_canonical(canonical, resolution=resolution)
    return ReaderDecode(
        pixels=pixels,
        source_colorspace=UNKNOWN_COLORSPACE,
        bit_depth=bit_depth,
        resolution=resolution,
        metadata=canonical,
        raw_header=raw,
    )


def _metadata_via_ffprobe(ffprobe: Path, path: Path) -> ImageMetadata:
    """Read container header metadata via ffprobe (no pixel decode)."""
    doc = _probe(ffprobe, path)
    canonical, resolution, bit_depth, raw = _canonical_and_raw(doc, path)
    return ImageMetadata(
        colorspace=UNKNOWN_COLORSPACE,
        source_colorspace=UNKNOWN_COLORSPACE,
        bit_depth=bit_depth,
        resolution=resolution,
        metadata=canonical,
        raw_header=raw,
    )


class FFmpegReader(Reader):
    """Editorial/delivery container reader (.mov, .mp4, .m4v, .avi, .mkv)."""

    priority = 8  # after camera-raw (ARRI 5, RED 6), before OIIO (10)

    def can_read(self, path: Path) -> bool:
        return path.suffix.lower() in _FFMPEG_EXTENSIONS

    def read_header_only(self, path: Path) -> ImageMetadata:
        p = path.expanduser().resolve()
        if not p.is_file():
            raise FileNotFoundError(str(p))
        ffprobe = _ffprobe_path()
        if ffprobe is None:
            raise FFmpegUnavailableError(_NO_BACKEND_MSG)
        return _metadata_via_ffprobe(ffprobe, p)

    def read_pixels(self, path: Path, **opts: Any) -> ReaderDecode:
        frame_index = int(opts.get("frame_index", 0))
        p = path.expanduser().resolve()
        if not p.is_file():
            raise FileNotFoundError(str(p))
        ffmpeg, ffprobe = _ffmpeg_path(), _ffprobe_path()
        if ffmpeg is None or ffprobe is None:
            raise FFmpegUnavailableError(_NO_BACKEND_MSG)
        return _decode_via_ffmpeg(ffmpeg, ffprobe, p, frame_index)


__all__ = [
    "FORGE_FFMPEG_PATH_ENV",
    "FORGE_FFPROBE_PATH_ENV",
    "FFmpegReader",
    "_ffmpeg_available",
    "_ffmpeg_path",
    "_ffprobe_path",
]
