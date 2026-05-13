"""RED R3D (``.r3d``) reader — two backends, same public surface.

Backends (selected at call time, in order):

1. **R3D SDK pybind11** — ``FORGE_RED_SDK_PATH`` points at the SDK shared
   library and the eventual ``forge-io-red`` sibling package is importable.
   Fast in-process decode. Pending real implementation (the ``ctypes.CDLL``
   gate here only confirms the library loads; the sibling does the actual
   binding work per ``RED_BINDING_PLAN.md``).

   Caveat: the ``REDR3D.dylib`` shipped inside consumer host apps
   (REDCINE-X, DaVinci Resolve, Nuke, Mocha, Fusion, SynthEyes, BLG) is
   symbol-stripped — only obfuscated ``R3D_xxx`` trampolines export, no
   ``R3DSDK::*``. The coarse load gate would pass on those, but the sibling
   binding will fail to resolve symbols. Point ``FORGE_RED_SDK_PATH`` at the
   actual SDK download from the RED Developer Program, not a host-app
   dylib.

2. **REDline subprocess** — ``FORGE_RED_REDLINE_PATH`` points at the
   ``REDline`` binary bundled with REDCINE-X PRO (e.g.
   ``/Applications/REDCINE-X Professional/REDCINE-X PRO.app/Contents/MacOS/REDline``).
   forge-io shells out per call: decodes one frame to REDWideGamutRGB
   scene-linear half-float EXR (``--format 2 --res 1 --colorSpace 25
   --gammaCurve -1 --useMeta``), reads it back via OIIO, returns
   ``source_colorspace="REDWideGamutRGB/linear"``. Downstream OCIO transforms
   operate on that known intermediate. ``read_metadata`` uses
   ``--printMeta 1`` (the ``Key:\\tValue`` "normal" format) for header-only
   reads (no pixel decode).

If neither backend is available, ``RedSdkUnavailableError`` names both env
vars so the user knows their options.

forge-io does **not** redistribute the R3D SDK or REDline. Both are
user-installed.

Decode contract
---------------
``read_pixels`` always pins REDline output to **REDWideGamutRGB primaries**
(``--colorSpace 25``) and **linear transfer** (``--gammaCurve -1``)
regardless of source. For IPP2 clips this is the natural default; for
Legacy clips REDline applies its internal Legacy→IPP2 transform.
``source_colorspace`` is reported as ``"REDWideGamutRGB/linear"`` either
way — paralleling the ARRI ART-CMD backend's ``"AP0/D60/linear"`` posture:
trust the vendor's authoritative color science, land downstream OCIO on a
known wide-gamut linear intermediate.

The ``read()`` path uses a single REDline invocation. Canonical metadata
(resolution, framerate, pixel aspect, timecode) is harvested from the
EXR's ``extra_attribs`` REDline forwards there (``FrameWidth``,
``FrameHeight``, ``framesPerSecond`` rational, ``PixelAspectRatio``,
``TOD TC Start``). The ``read_metadata()`` path uses ``--printMeta 1``
only and parses its ``Key:\\tValue`` text — no EXR written.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from forge_io._types import ImageMetadata, merge_canonical
from forge_io.exceptions import ImageDecodeError, RedSdkUnavailableError
from forge_io.readers._base import Reader, ReaderDecode

_RED_EXTENSIONS = frozenset({".r3d"})

FORGE_RED_SDK_PATH_ENV = "FORGE_RED_SDK_PATH"
FORGE_RED_REDLINE_PATH_ENV = "FORGE_RED_REDLINE_PATH"

# REDline invocation defaults. Scene-linear REDWideGamutRGB is the natural
# IPP2 output and the standard wide-gamut intermediate for VFX; for non-IPP2
# clips REDline applies its internal transform to the same target.
_REDLINE_COLORSPACE_CODE = "25"  # REDWideGamutRGB (per `REDline --help`)
_REDLINE_GAMMA_CODE = "-1"  # linear (per `REDline --help`)
_REDLINE_FORMAT_EXR = "2"  # OpenEXR
_REDLINE_RES_FULL = "1"  # full resolution

# Reported as source_colorspace on the public Image — what REDline decoded to.
_REDLINE_SOURCE_COLORSPACE = "REDWideGamutRGB/linear"

_NO_BACKEND_MSG = (
    "RED R3D (.r3d) requires either:\n"
    f"  (1) the R3D SDK ({FORGE_RED_SDK_PATH_ENV} pointing at the shared\n"
    "      library + the forge-io-red sibling package — decode pending), or\n"
    f"  (2) REDline ({FORGE_RED_REDLINE_PATH_ENV} pointing at the REDline\n"
    "      binary, e.g.\n"
    "      /Applications/REDCINE-X Professional/REDCINE-X PRO.app/Contents/MacOS/REDline\n"
    "      — subprocess-based decode via REDCINE-X PRO).\n"
    "Neither is configured in this environment."
)


def _red_sdk_available() -> bool:
    """Coarse gate: ``FORGE_RED_SDK_PATH`` points at a loadable shared library.

    Caveat: this only verifies the file dlopens — it does not verify the
    library exports ``R3DSDK::*`` symbols. Host-app-bundled ``REDR3D.dylib``
    (REDCINE-X / Resolve / Nuke / Mocha / Fusion / SynthEyes / BLG) is
    symbol-stripped and would false-positive here, but the eventual
    ``forge-io-red`` sibling will fail to bind against it. Point this env var
    at the actual SDK download under the RED Developer Program.
    """
    env_path = os.environ.get(FORGE_RED_SDK_PATH_ENV)
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


def _red_redline_path() -> Path | None:
    """Return the REDline binary path when ``FORGE_RED_REDLINE_PATH`` is usable."""
    env_path = os.environ.get(FORGE_RED_REDLINE_PATH_ENV)
    if not env_path:
        return None
    p = Path(env_path).expanduser()
    if not p.is_file() or not os.access(p, os.X_OK):
        return None
    return p


def _red_redline_available() -> bool:
    return _red_redline_path() is not None


def _run_redline(
    redline: Path,
    args: Iterable[str],
    cwd: Path,
    accept_codes: tuple[int, ...] = (0,),
) -> subprocess.CompletedProcess[str]:
    """Run REDline with stderr captured for error reporting.

    REDline returns exit code **1** for metadata-only invocations
    (``--printMeta`` without a decode), even on success — callers that use
    those modes must pass ``accept_codes=(0, 1)``. Decode invocations
    return 0 normally; keep the default for those.
    """
    try:
        proc = subprocess.run(
            [str(redline), *args],
            cwd=str(cwd),
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as e:
        raise ImageDecodeError(cwd, f"REDline binary not found at {redline}") from e
    if proc.returncode not in accept_codes:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-5:]
        raise ImageDecodeError(
            cwd,
            f"REDline failed (exit {proc.returncode}): {' | '.join(tail) or 'no stderr'}",
        )
    return proc


def _read_decoded_exr(
    exr_path: Path,
) -> tuple[Any, int, tuple[int, int], dict[str, Any]]:
    """Decode the EXR REDline wrote, returning ``(pixels, bits, (w,h), raw_header)``."""
    import numpy as np
    import OpenImageIO as oiio

    buf = oiio.ImageBuf(str(exr_path), 0, 0)
    if buf.has_error:
        raise ImageDecodeError(exr_path, buf.geterror() or "ImageBuf error on REDline output")
    spec = buf.spec()
    if spec.nchannels < 3:
        raise ImageDecodeError(exr_path, f"REDline EXR has {spec.nchannels} channels, need 3")
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


def _canonical_from_exr_attribs(
    raw: dict[str, Any], resolution: tuple[int, int]
) -> dict[str, Any]:
    """Build canonical metadata from REDline-written EXR ``extra_attribs``.

    REDline embeds clip metadata using its own attribute names alongside
    EXR-standard ones. We prefer the EXR-standard ``framesPerSecond``
    rational (``(num, denom)``) over REDline's float ``FPS`` for exact
    24000/1001-style rates.
    """
    framerate: float | None = None
    fps_pair = raw.get("framesPerSecond")
    if isinstance(fps_pair, tuple) and len(fps_pair) == 2:
        num, denom = fps_pair
        if isinstance(num, int) and isinstance(denom, int) and denom != 0:
            framerate = num / denom
    if framerate is None:
        fps = raw.get("FPS")
        if isinstance(fps, (int, float)):
            framerate = float(fps)

    pixel_aspect: float | None = None
    par = raw.get("PixelAspectRatio")
    if isinstance(par, (int, float)):
        pixel_aspect = float(par)

    # Forge-io's canonical timecode is the absolute / TOD TC by convention
    # (matches Clip::Timecode(0) and RMD_START_ABSOLUTE_TIMECODE per the
    # binding plan). Edge / run-record TC stays available in raw_header.
    timecode: str | None = None
    tc = raw.get("TOD TC Start")
    if isinstance(tc, str) and tc:
        timecode = tc

    return merge_canonical(
        None,
        resolution=resolution,
        pixel_aspect=pixel_aspect,
        timecode=timecode,
        framerate=framerate,
    )


def _decode_via_redline(redline: Path, path: Path) -> ReaderDecode:
    """Decode one frame of an ``.R3D`` clip via REDline subprocess.

    v0.3 decodes the **first frame** of the clip (``--start 0 --end 0``);
    a future kwarg may expose per-frame selection. The decode contract:
    full-resolution REDWideGamutRGB scene-linear half-float OpenEXR,
    written to a temp dir and read back via OIIO. Canonical metadata is
    harvested from the EXR's ``extra_attribs`` REDline forwards there.
    """
    with tempfile.TemporaryDirectory(prefix="forge-io-red-") as tmp:
        tmp_dir = Path(tmp)
        _run_redline(
            redline,
            [
                "--useMeta",
                "--i",
                str(path),
                "--start",
                "0",
                "--end",
                "0",
                "--format",
                _REDLINE_FORMAT_EXR,
                "--res",
                _REDLINE_RES_FULL,
                "--colorSpace",
                _REDLINE_COLORSPACE_CODE,
                "--gammaCurve",
                _REDLINE_GAMMA_CODE,
                "--o",
                "frame",
                "--outDir",
                str(tmp_dir),
                "--pad",
                "6",
            ],
            cwd=tmp_dir,
        )
        exrs = sorted(tmp_dir.glob("frame.*.exr"))
        if not exrs:
            raise ImageDecodeError(path, "REDline produced no EXR output")
        pixels, bits, res, raw = _read_decoded_exr(exrs[0])
    canonical = _canonical_from_exr_attribs(raw, res)
    return ReaderDecode(
        pixels=pixels,
        source_colorspace=_REDLINE_SOURCE_COLORSPACE,
        bit_depth=bits,
        resolution=res,
        metadata=canonical,
        raw_header=raw,
    )


def _parse_redline_keyvalue(text: str) -> dict[str, str]:
    """Parse REDline ``--printMeta 1`` output: ``Key:\\tValue`` per line.

    Some keys have empty values (``Camera Network Name:``) — those collapse
    to empty strings. Lines without a colon are skipped (mostly the leading
    log-banner lines on stderr, but tolerate stray ones on stdout too).
    """
    out: dict[str, str] = {}
    for line in text.splitlines():
        if "\t" in line and ":" in line:
            key_part, _, value = line.partition("\t")
            key = key_part.rstrip(":").strip()
            if key:
                out[key] = value.strip()
        elif ":" in line:
            key, _, value = line.partition(":")
            key = key.strip()
            if key:
                out[key] = value.strip()
    return out


def _canonical_from_redline_fields(
    fields: dict[str, str],
) -> tuple[dict[str, Any], tuple[int, int] | None]:
    """Build canonical metadata from a parsed ``--printMeta 1`` field map."""

    def _int(key: str) -> int | None:
        v = fields.get(key, "").strip()
        try:
            return int(v) if v else None
        except ValueError:
            return None

    def _float(key: str) -> float | None:
        v = fields.get(key, "").strip()
        try:
            return float(v) if v else None
        except ValueError:
            return None

    width = _int("Frame Width")
    height = _int("Frame Height")
    resolution = (width, height) if width is not None and height is not None else None
    framerate = _float("FPS")
    pixel_aspect = _float("Pixel Aspect Ratio")
    # Canonical timecode is the absolute / TOD TC; Edge TC remains accessible
    # through raw_header.
    timecode = fields.get("Abs TC") or None

    canonical = merge_canonical(
        None,
        resolution=resolution,
        pixel_aspect=pixel_aspect,
        timecode=timecode,
        framerate=framerate,
    )
    return canonical, resolution


def _metadata_via_redline(redline: Path, path: Path) -> ImageMetadata:
    """Read REDline header-only metadata via ``--printMeta 1`` (no pixel decode)."""
    proc = _run_redline(
        redline,
        ["--i", str(path), "--useMeta", "--printMeta", "1"],
        cwd=path.parent,
        accept_codes=(0, 1),
    )
    fields = _parse_redline_keyvalue(proc.stdout)
    canonical, resolution = _canonical_from_redline_fields(fields)
    return ImageMetadata(
        colorspace=_REDLINE_SOURCE_COLORSPACE,
        source_colorspace=_REDLINE_SOURCE_COLORSPACE,
        bit_depth=16,
        resolution=resolution,
        metadata=canonical,
        raw_header={"redline_meta": fields},
    )


class RedRawReader(Reader):
    """R3D container reader (.r3d)."""

    priority = 6  # after ArriRawReader (5), before OIIOReader (10)

    def can_read(self, path: Path) -> bool:
        return path.suffix.lower() in _RED_EXTENSIONS

    def read_header_only(self, path: Path) -> ImageMetadata:
        p = path.expanduser().resolve()
        if not p.is_file():
            raise FileNotFoundError(str(p))
        if _red_sdk_available():
            raise NotImplementedError(
                "RED SDK gate open but pybind11 sibling package is not yet implemented"
            )
        redline = _red_redline_path()
        if redline is not None:
            return _metadata_via_redline(redline, p)
        raise RedSdkUnavailableError(_NO_BACKEND_MSG)

    def read_pixels(self, path: Path) -> ReaderDecode:
        p = path.expanduser().resolve()
        if not p.is_file():
            raise FileNotFoundError(str(p))
        if _red_sdk_available():
            raise NotImplementedError(
                "RED SDK gate open but pybind11 sibling package is not yet implemented"
            )
        redline = _red_redline_path()
        if redline is not None:
            return _decode_via_redline(redline, p)
        raise RedSdkUnavailableError(_NO_BACKEND_MSG)


__all__ = [
    "FORGE_RED_REDLINE_PATH_ENV",
    "FORGE_RED_SDK_PATH_ENV",
    "RedRawReader",
    "_red_redline_available",
    "_red_redline_path",
    "_red_sdk_available",
]
