"""forge-io: read pixels via OIIO with optional OCIO transforms."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from forge_io._color import apply_working_space
from forge_io._patterns import resolve_pattern
from forge_io._registry import get_reader
from forge_io._types import UNKNOWN_COLORSPACE, Image, ImageMetadata
from forge_io.exceptions import (
    AmbiguousExrError,
    ArriSdkUnavailableError,
    ForgeIOError,
    ImageDecodeError,
    OCIOConfigError,
    OCIOTransformError,
    RedSdkUnavailableError,
    SonyUnsupportedError,
    UnknownColorspaceTransformError,
    UnsupportedFileError,
)


def read(
    path: str | Path,
    *,
    working_space: str | None = None,
    assume_source: str | None = None,
    ocio_config: str | Path | None = None,
    frame_index: int = 0,
) -> Image:
    """Read an image file and return float32 RGB (H, W, 3).

    ``frame_index`` is the 0-based intra-clip frame for single-file raw
    clips (e.g. RED ``.r3d``). Readers that don't support intra-clip frame
    selection (OIIO, ARRI single-frame) ignore it. For image sequences,
    use :func:`read_frame` (which resolves a pattern + index to a path)
    instead.
    """
    p = Path(path).expanduser().resolve()
    reader = get_reader(p)
    dec = reader.read_pixels(p, frame_index=frame_index)
    pixels = np.ascontiguousarray(dec.pixels, dtype=np.float32)
    colorspace = dec.source_colorspace
    if working_space is not None:
        pixels, colorspace = apply_working_space(
            pixels,
            source_colorspace=dec.source_colorspace,
            working_space=working_space,
            assume_source=assume_source,
            ocio_config=ocio_config,
        )
    return Image(
        pixels=pixels,
        colorspace=colorspace,
        source_colorspace=dec.source_colorspace,
        bit_depth=dec.bit_depth,
        resolution=dec.resolution,
        metadata=dict(dec.metadata),
        raw_header=dict(dec.raw_header),
    )


def read_frame(
    pattern: str,
    frame_idx: int,
    *,
    working_space: str | None = None,
    assume_source: str | None = None,
    ocio_config: str | Path | None = None,
) -> Image:
    """Resolve ``pattern`` with ``frame_idx`` and ``read`` the result."""
    return read(
        resolve_pattern(pattern, frame_idx),
        working_space=working_space,
        assume_source=assume_source,
        ocio_config=ocio_config,
    )


def read_metadata(path: str | Path) -> ImageMetadata:
    """Read header metadata without decoding pixels (OIIO uses ``ImageInput`` + ``spec`` only)."""
    p = Path(path).expanduser().resolve()
    return get_reader(p).read_header_only(p)


__all__ = [
    "AmbiguousExrError",
    "ArriSdkUnavailableError",
    "ForgeIOError",
    "Image",
    "ImageDecodeError",
    "ImageMetadata",
    "OCIOConfigError",
    "OCIOTransformError",
    "RedSdkUnavailableError",
    "SonyUnsupportedError",
    "UnknownColorspaceTransformError",
    "UnsupportedFileError",
    "UNKNOWN_COLORSPACE",
    "read",
    "read_frame",
    "read_metadata",
    "resolve_pattern",
]
