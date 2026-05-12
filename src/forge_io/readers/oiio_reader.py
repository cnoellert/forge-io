"""OpenImageIO-backed reader for raster formats (EXR, DPX, PNG, JPEG, TIFF)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from forge_io._types import UNKNOWN_COLORSPACE, ImageMetadata, merge_canonical
from forge_io.exceptions import AmbiguousExrError, ImageDecodeError
from forge_io.readers._base import Reader, ReaderDecode

_Decode = tuple[np.ndarray, int, tuple[int, int], str, dict[str, Any], dict[str, Any]]

_OIIO_EXTENSIONS = {".exr", ".dpx", ".png", ".jpg", ".jpeg", ".tif", ".tiff"}

_BEAUTY_LAYER_ALLOWLIST = frozenset({"rgba", "rgb", "beauty"})


def _parse_layer_suffix(channel: str) -> tuple[str, str]:
    if "." not in channel:
        return "", channel
    layer, suf = channel.rsplit(".", 1)
    return layer, suf


def _unprefixed_rgb_candidate(channel_names: list[str]) -> list[str] | None:
    names = set(channel_names)
    if {"R", "G", "B"}.issubset(names):
        return ["R", "G", "B"]
    return None


def _single_subimage_beauty(channel_names: list[str]) -> list[str] | None:
    """At most one beauty triple per subimage; raises if multiple allowlisted layers qualify."""
    if _unprefixed_rgb_candidate(channel_names) is not None:
        return ["R", "G", "B"]
    by_layer_lower: dict[str, list[str]] = {}
    for ch in channel_names:
        layer, _suf = _parse_layer_suffix(ch)
        if not layer:
            continue
        by_layer_lower.setdefault(layer.lower(), []).append(ch)
    matches: list[list[str]] = []
    for layer_lower, chs in by_layer_lower.items():
        if layer_lower not in _BEAUTY_LAYER_ALLOWLIST:
            continue
        m: dict[str, str] = {}
        for ch in chs:
            lay, suf = _parse_layer_suffix(ch)
            if lay.lower() != layer_lower:
                continue
            su = suf.upper()
            if su == "R":
                m["R"] = ch
            elif su == "G":
                m["G"] = ch
            elif su == "B":
                m["B"] = ch
        if len(m) == 3:
            matches.append([m["R"], m["G"], m["B"]])
    if len(matches) == 0:
        return None
    if len(matches) > 1:
        raise AmbiguousExrError("Multiple allowlisted beauty layer candidates in one EXR subimage")
    return matches[0]


def _enumerate_subimage_channel_layout(path: Path) -> list[tuple[int, list[str]]]:
    """Copy channel names per subimage; do not retain ``ImageSpec`` past ``close()``."""
    import OpenImageIO as oiio

    inp = oiio.ImageInput.open(str(path))
    if inp is None:
        raise ImageDecodeError(path, oiio.geterror() or "ImageInput.open failed")
    out: list[tuple[int, list[str]]] = []
    sub = 0
    try:
        while True:
            spec = inp.spec()
            out.append((sub, list(spec.channelnames)))
            if not inp.seek_subimage(sub + 1, 0):
                break
            sub += 1
    finally:
        inp.close()
    return out


def _select_exr_beauty_channels(path: Path) -> tuple[int, list[str]]:
    layout = _enumerate_subimage_channel_layout(path)
    candidates: list[tuple[int, list[str]]] = []
    for sub, chans in layout:
        triple = _single_subimage_beauty(chans)
        if triple is not None:
            candidates.append((sub, triple))
    if len(candidates) == 0:
        raise AmbiguousExrError(f"No beauty RGB selection for EXR: {path}")
    if len(candidates) > 1:
        raise AmbiguousExrError(
            f"Ambiguous beauty RGB selection ({len(candidates)} candidates): {path}"
        )
    return candidates[0]


def _param_value(pv: Any) -> Any:
    v = getattr(pv, "value", None)
    if callable(v):
        return v()
    return v


def _spec_to_raw_header(spec: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    try:
        for a in spec.extra_attribs:
            out[str(a.name)] = _param_value(a)
    except Exception:
        pass
    return out


def _format_bits(spec: Any) -> int:
    import OpenImageIO as oiio

    fmt = spec.channelformat(0)
    if fmt.basesize() == 1:
        bt = fmt.basetype
        if bt == oiio.UINT8:
            return 8
        if bt == oiio.UINT16:
            return 16
        if bt == oiio.HALF:
            return 16
        if bt == oiio.FLOAT:
            return 32
    return 32


def _extra_attr_first(spec: Any, *keys: str) -> Any:
    want = set(keys)
    try:
        for a in spec.extra_attribs:
            if str(a.name) in want:
                return _param_value(a)
    except Exception:
        pass
    return None


def _encoded_bit_depth(spec: Any) -> int:
    """Prefer file-declared bits (e.g. ``oiio:BitsPerSample`` for DPX/TIFF); else dtype width."""
    for key in ("oiio:BitsPerSample", "BitsPerSample", "openexr:bitsPerPixel"):
        v = _extra_attr_first(spec, key)
        if v is None:
            continue
        try:
            if isinstance(v, (list, tuple)):
                return int(v[0])
            return int(v)
        except (TypeError, ValueError):
            continue
    return _format_bits(spec)


def _declared_colorspace_from_spec(spec: Any, path: Path) -> str:
    suf = path.suffix.lower()
    if suf == ".dpx":
        return UNKNOWN_COLORSPACE
    for key in ("oiio:ColorSpace", "colorspace"):
        v = _extra_attr_first(spec, key)
        if v is not None and str(v).strip():
            return str(v).strip()
    if suf == ".exr":
        return UNKNOWN_COLORSPACE
    if suf in {".png", ".jpg", ".jpeg", ".tif", ".tiff"}:
        return UNKNOWN_COLORSPACE
    return UNKNOWN_COLORSPACE


def _spec_at_subimage(path: Path, subimage: int) -> Any:
    """Return ``ImageSpec`` for ``subimage`` (``ImageInput`` is closed afterward)."""
    import OpenImageIO as oiio

    inp = oiio.ImageInput.open(str(path))
    if inp is None:
        raise ImageDecodeError(path, oiio.geterror() or "ImageInput.open failed")
    try:
        if subimage > 0 and not inp.seek_subimage(subimage, 0):
            raise ImageDecodeError(path, f"seek_subimage({subimage}, 0) failed")
        return inp.spec()
    finally:
        inp.close()


def _read_header_metadata(path: Path) -> ImageMetadata:
    """Header-only: ``ImageInput`` + ``spec``, no ``ImageBuf`` / pixel decode."""
    suf = path.suffix.lower()
    if suf == ".exr":
        sub, _triple = _select_exr_beauty_channels(path)
        spec = _spec_at_subimage(path, sub)
    else:
        spec = _spec_at_subimage(path, 0)
    w, h = int(spec.width), int(spec.height)
    raw = _spec_to_raw_header(spec)
    bits = _encoded_bit_depth(spec)
    src_cs = _declared_colorspace_from_spec(spec, path)
    if suf == ".dpx":
        src_cs = UNKNOWN_COLORSPACE
    meta = merge_canonical(None, resolution=(w, h))
    return ImageMetadata(
        colorspace=src_cs,
        source_colorspace=src_cs,
        bit_depth=bits,
        resolution=(w, h),
        metadata=meta,
        raw_header=raw,
    )


def _read_exr_rgb(path: Path) -> _Decode:
    import OpenImageIO as oiio

    sub, chans = _select_exr_beauty_channels(path)
    buf = oiio.ImageBuf(str(path), int(sub), 0)
    if buf.has_error:
        raise ImageDecodeError(path, buf.geterror() or "ImageBuf error")
    spec = buf.spec()
    outb = oiio.ImageBuf()
    ok = oiio.ImageBufAlgo.channels(outb, buf, tuple(chans), ("R", "G", "B"))
    if not ok:
        raise ImageDecodeError(path, oiio.geterror() or "ImageBufAlgo.channels failed")
    pixels = outb.get_pixels(oiio.FLOAT)
    if pixels is None:
        raise ImageDecodeError(path, "get_pixels returned None")
    arr = np.asarray(pixels, dtype=np.float32)
    if arr.ndim == 2:
        arr = arr[..., np.newaxis]
    if arr.shape[-1] != 3:
        raise ImageDecodeError(path, f"expected 3 channels, got shape {arr.shape}")
    h, w, _ = arr.shape
    raw = _spec_to_raw_header(spec)
    src_cs = _declared_colorspace_from_spec(spec, path)
    bits = _encoded_bit_depth(spec)
    return arr, bits, (w, h), src_cs, merge_canonical(None, resolution=(w, h)), raw


def _read_flat_rgb(path: Path) -> _Decode:
    """Single-subimage RGB decode for non-EXR raster formats."""
    import OpenImageIO as oiio

    buf = oiio.ImageBuf(str(path), 0, 0)
    if buf.has_error:
        raise ImageDecodeError(path, buf.geterror() or "ImageBuf error")
    spec = buf.spec()
    if spec.nchannels < 3:
        raise ImageDecodeError(path, f"expected at least 3 channels, got {spec.nchannels}")
    outb = oiio.ImageBuf()
    ch0, ch1, ch2 = spec.channelnames[:3]
    ok = oiio.ImageBufAlgo.channels(outb, buf, (ch0, ch1, ch2), ("R", "G", "B"))
    if not ok:
        raise ImageDecodeError(path, oiio.geterror() or "ImageBufAlgo.channels failed")
    pixels = outb.get_pixels(oiio.FLOAT)
    arr = np.asarray(pixels, dtype=np.float32)
    if arr.ndim == 2:
        arr = arr[..., np.newaxis]
    h, w, _ = arr.shape
    raw = _spec_to_raw_header(spec)
    src_cs = _declared_colorspace_from_spec(spec, path)
    bits = _encoded_bit_depth(spec)
    return arr, bits, (w, h), src_cs, merge_canonical(None, resolution=(w, h)), raw


class OIIOReader(Reader):
    priority = 10

    def can_read(self, path: Path) -> bool:
        return path.suffix.lower() in _OIIO_EXTENSIONS

    def read_header_only(self, path: Path) -> ImageMetadata:
        return _read_header_metadata(path)

    def read_pixels(self, path: Path) -> ReaderDecode:
        suf = path.suffix.lower()
        if suf == ".exr":
            pixels, bits, res, src_cs, meta, raw = _read_exr_rgb(path)
        else:
            pixels, bits, res, src_cs, meta, raw = _read_flat_rgb(path)
        if suf == ".dpx":
            src_cs = UNKNOWN_COLORSPACE
        return ReaderDecode(
            pixels=pixels,
            source_colorspace=src_cs,
            bit_depth=bits,
            resolution=res,
            metadata=meta,
            raw_header=raw,
        )
