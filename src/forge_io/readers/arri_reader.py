"""ARRIRAW (``.ari`` / ``.arx``) reader — two backends, same public surface.

Backends (selected at call time, in order):

1. **ARRI Image SDK pybind11** — ``FORGE_ARRI_SDK_PATH`` points at the SDK
   shared library and the eventual ``forge-io-arri`` sibling package is
   importable. Fast in-process decode. Pending real implementation (the
   ``ctypes.CDLL`` gate here only confirms the library loads; the sibling
   does the actual binding work).

2. **ART-CMD subprocess** — ``FORGE_ARRI_ART_PATH`` points at the
   ``art-cmd`` binary from ARRI Reference Tools. forge-io shells out for
   each ``read`` / ``read_metadata`` call. Decodes single-frame ``.ari``
   or HDE-compressed ``.arx`` from a sequence directory; metadata via
   ``art-cmd export``. Authoritative ARRI color science (same Image SDK
   under the hood). Trade-off: per-call process spawn + EXR roundtrip
   to disk.

If neither backend is available, ``ArriSdkUnavailableError`` names both
env vars so the user knows their options.

forge-io does **not** redistribute either the ARRI Image SDK or ART-CMD.
Both are user-installed.

SDK discovery (option 1)
------------------------
Set ``FORGE_ARRI_SDK_PATH`` to the absolute path of the SDK shared library
(e.g. ``libArriImageSdk.dylib`` / ``.so`` / ``.dll`` — exact filename from
your Partner Program install). The gate (``_arri_sdk_available``) does a
coarse ``ctypes.CDLL`` load only.

ART-CMD discovery (option 2)
----------------------------
Set ``FORGE_ARRI_ART_PATH`` to the absolute path of the ``art-cmd`` binary
(e.g. ``/Applications/art-cmd_1.0.0_macos_universal/bin/art-cmd`` on macOS).
The gate verifies the file exists and is executable. ART-CMD is a free
download from `ARRI Reference Tools`_.

On macOS, Safari-downloaded ART-CMD bundles are quarantined; if the binary
fails to load its dylibs with "code signature ... not valid", clear the
quarantine with::

    xattr -dr com.apple.quarantine /Applications/art-cmd_*

.. _ARRI Reference Tools: https://www.arri.com/en/learn-help/learn-help-camera-system/tools/arri-reference-tool

Sequence offsets
----------------
``art-cmd`` takes a sequence directory (or printf pattern) plus
``--start N --duration M`` where N is the **offset within the clip**,
not the absolute frame number. forge-io scans the directory once to
find the lowest frame number and computes the offset for the requested
frame.

Decode contract
---------------
``read_pixels`` always requests ``AP0/D60/linear`` ACES AP0 scene-linear
output from ART-CMD (uncompressed 16-bit half-float EXR), and reports
``source_colorspace="AP0/D60/linear"``. Downstream OCIO transforms
operate on a known, camera-independent space. CPU render platform
(``--render-platform cpu``) is forced for bit-stable VFX output.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from forge_io._types import ImageMetadata, merge_canonical
from forge_io.exceptions import ArriSdkUnavailableError, ImageDecodeError
from forge_io.readers._base import Reader, ReaderDecode

_ARRI_RAW_EXTENSIONS = frozenset({".ari", ".arx"})

FORGE_ARRI_SDK_PATH_ENV = "FORGE_ARRI_SDK_PATH"
FORGE_ARRI_ART_PATH_ENV = "FORGE_ARRI_ART_PATH"

# ART-CMD invocation defaults. Scene-linear ACES AP0 is ART's own default and
# the standard VFX intermediate; CPU platform is forced for deterministic decode.
_ART_TARGET_COLORSPACE = "AP0/D60/linear"
_ART_VIDEO_CODEC = "exr_uncompressed/f16"
_ART_RENDER_PLATFORM = "cpu"

# Reported as source_colorspace on the public Image — matches what ART decoded to.
_ART_SOURCE_COLORSPACE = _ART_TARGET_COLORSPACE

_FRAME_NUMBER_RE = re.compile(r"\.(\d+)\.(?:ari|arx)$", re.IGNORECASE)

_NO_BACKEND_MSG = (
    "ARRIRAW (.ari / .arx) requires either:\n"
    f"  (1) the ARRI Image SDK ({FORGE_ARRI_SDK_PATH_ENV} pointing at the shared\n"
    "      library + the forge-io-arri sibling package — decode pending), or\n"
    f"  (2) ART-CMD ({FORGE_ARRI_ART_PATH_ENV} pointing at the art-cmd binary\n"
    "      from ARRI Reference Tools — subprocess-based decode).\n"
    "Neither is configured in this environment."
)


def _arri_sdk_available() -> bool:
    """Coarse gate: ``FORGE_ARRI_SDK_PATH`` points at a loadable shared library."""
    env_path = os.environ.get(FORGE_ARRI_SDK_PATH_ENV)
    if not env_path:
        return False
    lib_path = Path(env_path).expanduser()
    if not lib_path.is_file():
        return False
    try:
        import ctypes

        ctypes.CDLL(str(lib_path))
    except OSError:
        return False
    return True


def _arri_art_path() -> Path | None:
    """Return the ART-CMD binary path when ``FORGE_ARRI_ART_PATH`` is usable."""
    env_path = os.environ.get(FORGE_ARRI_ART_PATH_ENV)
    if not env_path:
        return None
    p = Path(env_path).expanduser()
    if not p.is_file() or not os.access(p, os.X_OK):
        return None
    return p


def _arri_art_available() -> bool:
    return _arri_art_path() is not None


def _parse_frame_number(path: Path) -> int:
    """Extract the trailing numeric frame index from an .ari/.arx filename."""
    m = _FRAME_NUMBER_RE.search(path.name)
    if m is None:
        raise ImageDecodeError(path, "no trailing frame number in .ari/.arx filename")
    return int(m.group(1))


def _clip_min_frame(clip_dir: Path, stem_base: str, ext: str) -> int:
    """Lowest frame number among ``stem_base.NNNNNNN.ext`` siblings in ``clip_dir``."""
    pattern = re.compile(rf"^{re.escape(stem_base)}\.(\d+){re.escape(ext)}$", re.IGNORECASE)
    frames: list[int] = []
    try:
        for entry in clip_dir.iterdir():
            m = pattern.match(entry.name)
            if m is not None:
                frames.append(int(m.group(1)))
    except OSError as e:
        raise ImageDecodeError(clip_dir, f"cannot list clip directory: {e}") from e
    if not frames:
        raise ImageDecodeError(clip_dir, f"no {ext} frames matching {stem_base!r} in directory")
    return min(frames)


def _printf_pattern_for(path: Path, width: int = 7) -> str:
    """Build the ART-CMD printf input pattern (e.g. ``CLIP.%07d.arx``) for a clip frame."""
    m = _FRAME_NUMBER_RE.search(path.name)
    if m is None:
        raise ImageDecodeError(path, "no trailing frame number in .ari/.arx filename")
    digits = m.group(1)
    width = len(digits)  # honour actual filename padding
    stem_base = path.name[: m.start()]
    return f"{stem_base}.%0{width}d{path.suffix.lower()}"


def _run_art_cmd(art_cmd: Path, args: Iterable[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    """Run ART-CMD with stderr captured for error reporting."""
    try:
        return subprocess.run(
            [str(art_cmd), *args],
            cwd=str(cwd),
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as e:
        tail = (e.stderr or e.stdout or "").strip().splitlines()[-5:]
        raise ImageDecodeError(
            cwd,
            f"art-cmd failed (exit {e.returncode}): {' | '.join(tail) or 'no stderr'}",
        ) from e
    except FileNotFoundError as e:
        raise ImageDecodeError(cwd, f"art-cmd binary not found at {art_cmd}") from e


def _read_decoded_exr(exr_path: Path) -> tuple[Any, int, tuple[int, int], dict[str, Any]]:
    """Decode the EXR ART-CMD wrote, returning (pixels, bits, (w,h), raw_header)."""
    import numpy as np
    import OpenImageIO as oiio

    buf = oiio.ImageBuf(str(exr_path), 0, 0)
    if buf.has_error:
        raise ImageDecodeError(exr_path, buf.geterror() or "ImageBuf error on art-cmd output")
    spec = buf.spec()
    if spec.nchannels < 3:
        raise ImageDecodeError(exr_path, f"art-cmd EXR has {spec.nchannels} channels, need 3")
    out = oiio.ImageBuf()
    ch0, ch1, ch2 = spec.channelnames[:3]
    if not oiio.ImageBufAlgo.channels(out, buf, (ch0, ch1, ch2), ("R", "G", "B")):
        raise ImageDecodeError(exr_path, oiio.geterror() or "channel select failed")
    pixels = out.get_pixels(oiio.FLOAT)
    arr = np.asarray(pixels, dtype=np.float32)
    if arr.ndim == 2:
        arr = arr[..., np.newaxis]
    h, w, _ = arr.shape
    raw: dict[str, Any] = {}
    try:
        for a in spec.extra_attribs:
            v = a.value
            raw[str(a.name)] = v() if callable(v) else v
    except Exception:  # noqa: BLE001
        pass
    return arr, 16, (w, h), raw


def _decode_via_art_cmd(art_cmd: Path, path: Path) -> ReaderDecode:
    """Decode one frame of an ``.ari`` / ``.arx`` clip via ART-CMD subprocess."""
    requested = _parse_frame_number(path)
    clip_dir = path.parent
    ext = path.suffix.lower()
    stem_base = path.name[: _FRAME_NUMBER_RE.search(path.name).start()]  # type: ignore[union-attr]
    clip_min = _clip_min_frame(clip_dir, stem_base, ext)
    offset = requested - clip_min
    if offset < 0:
        raise ImageDecodeError(path, f"requested frame {requested} precedes clip start {clip_min}")
    pattern = _printf_pattern_for(path)
    with tempfile.TemporaryDirectory(prefix="forge-io-arri-") as tmp:
        tmp_dir = Path(tmp)
        _run_art_cmd(
            art_cmd,
            [
                "process",
                "--input",
                str(clip_dir / pattern),
                "--start",
                str(offset),
                "--duration",
                "1",
                "--target-colorspace",
                _ART_TARGET_COLORSPACE,
                "--video-codec",
                _ART_VIDEO_CODEC,
                "--render-platform",
                _ART_RENDER_PLATFORM,
                "--output",
                str(tmp_dir / "%07d.exr"),
                "--logpath",
                "",
            ],
            cwd=tmp_dir,
        )
        exrs = sorted(tmp_dir.glob("*.exr"))
        if not exrs:
            raise ImageDecodeError(path, "art-cmd produced no EXR output")
        pixels, bits, res, raw = _read_decoded_exr(exrs[0])
    meta = merge_canonical(None, resolution=res)
    return ReaderDecode(
        pixels=pixels,
        source_colorspace=_ART_SOURCE_COLORSPACE,
        bit_depth=bits,
        resolution=res,
        metadata=meta,
        raw_header=raw,
    )


def _canonical_from_metadata_json(doc: dict[str, Any]) -> dict[str, Any]:
    """Best-effort extraction of canonical keys from ART-CMD's metadata JSON.

    ART-CMD's exported JSON is rich and the exact key layout depends on the
    source format and ART-CMD version. We try a small set of common paths
    and fall back to ``None`` for anything missing.
    """
    def _deep_get(*keys: str) -> Any:
        for key in keys:
            cur: Any = doc
            ok = True
            for part in key.split("."):
                if isinstance(cur, dict) and part in cur:
                    cur = cur[part]
                else:
                    ok = False
                    break
            if ok and cur is not None:
                return cur
        return None

    width = _deep_get("imageWidth", "width", "video.width")
    height = _deep_get("imageHeight", "height", "video.height")
    resolution = (int(width), int(height)) if width and height else None
    framerate = _deep_get("frameRate", "framerate", "video.frameRate")
    timecode = _deep_get("timecodeStart", "timecode", "video.timecodeStart")
    pixel_aspect = _deep_get("lensSqueezeFactor", "pixelAspect", "lens.lensSqueezeFactor")
    return merge_canonical(
        None,
        resolution=resolution,
        framerate=float(framerate) if framerate is not None else None,
        timecode=str(timecode) if timecode is not None else None,
        pixel_aspect=float(pixel_aspect) if pixel_aspect is not None else None,
    )


def _metadata_via_art_cmd(art_cmd: Path, path: Path) -> ImageMetadata:
    """Export ART-CMD metadata for a single frame without decoding pixels."""
    pattern = _printf_pattern_for(path)
    clip_dir = path.parent
    with tempfile.TemporaryDirectory(prefix="forge-io-arri-meta-") as tmp:
        tmp_dir = Path(tmp)
        meta_path = tmp_dir / "metadata.json"
        _run_art_cmd(
            art_cmd,
            [
                "export",
                "--input",
                str(clip_dir / pattern),
                "--skip-audio",
                "--skip-look",
                "--output",
                str(meta_path),
                "--logpath",
                "",
            ],
            cwd=tmp_dir,
        )
        if not meta_path.is_file():
            raise ImageDecodeError(path, "art-cmd produced no metadata.json")
        try:
            doc = json.loads(meta_path.read_text())
        except json.JSONDecodeError as e:
            raise ImageDecodeError(path, f"malformed metadata.json: {e}") from e
    canonical = _canonical_from_metadata_json(doc)
    resolution = canonical.get("resolution")
    return ImageMetadata(
        colorspace=_ART_SOURCE_COLORSPACE,
        source_colorspace=_ART_SOURCE_COLORSPACE,
        bit_depth=16,
        resolution=resolution,
        metadata=canonical,
        raw_header={"art_cmd_export": doc},
    )


class ArriRawReader(Reader):
    """ARRIRAW / HDE container reader (.ari and .arx)."""

    priority = 5  # before OIIOReader (10)

    def can_read(self, path: Path) -> bool:
        return path.suffix.lower() in _ARRI_RAW_EXTENSIONS

    def read_header_only(self, path: Path) -> ImageMetadata:
        p = path.expanduser().resolve()
        if not p.is_file():
            raise FileNotFoundError(str(p))
        if _arri_sdk_available():
            raise NotImplementedError(
                "ARRI SDK gate open but pybind11 sibling package is not yet implemented"
            )
        art = _arri_art_path()
        if art is not None:
            return _metadata_via_art_cmd(art, p)
        raise ArriSdkUnavailableError(_NO_BACKEND_MSG)

    def read_pixels(self, path: Path) -> ReaderDecode:
        p = path.expanduser().resolve()
        if not p.is_file():
            raise FileNotFoundError(str(p))
        if _arri_sdk_available():
            raise NotImplementedError(
                "ARRI SDK gate open but pybind11 sibling package is not yet implemented"
            )
        art = _arri_art_path()
        if art is not None:
            return _decode_via_art_cmd(art, p)
        raise ArriSdkUnavailableError(_NO_BACKEND_MSG)


# Convenience: surface the art-cmd discovery helper for tests / tooling.
__all__ = [
    "ArriRawReader",
    "FORGE_ARRI_ART_PATH_ENV",
    "FORGE_ARRI_SDK_PATH_ENV",
    "_arri_art_available",
    "_arri_art_path",
    "_arri_sdk_available",
]
