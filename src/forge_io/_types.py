"""Stable public types and canonical metadata keys."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import numpy as np

# Public contract: only this string means unknown colorspace for API outputs.
UNKNOWN_COLORSPACE = "unknown"

# Canonical metadata keys (semver-stable). Every key is always present; value may be None.
CANONICAL_METADATA_KEYS: tuple[str, ...] = (
    "resolution",
    "pixel_aspect",
    "timecode",
    "framerate",
)


def empty_canonical_metadata() -> dict[str, Any]:
    return {k: None for k in CANONICAL_METADATA_KEYS}


def merge_canonical(
    base: Mapping[str, Any] | None,
    *,
    resolution: tuple[int, int] | None = None,
    pixel_aspect: float | None = None,
    timecode: str | None = None,
    framerate: float | None = None,
) -> dict[str, Any]:
    out = dict(empty_canonical_metadata())
    if base:
        for k in CANONICAL_METADATA_KEYS:
            if k in base:
                out[k] = base[k]
    if resolution is not None:
        out["resolution"] = resolution
    if pixel_aspect is not None:
        out["pixel_aspect"] = pixel_aspect
    if timecode is not None:
        out["timecode"] = timecode
    if framerate is not None:
        out["framerate"] = framerate
    return out


@dataclass(frozen=True, slots=True)
class Image:
    """Decoded image: float32 RGB (H, W, 3), channels-last."""

    pixels: np.ndarray
    colorspace: str
    source_colorspace: str
    bit_depth: int
    resolution: tuple[int, int]
    metadata: dict[str, Any] = field(default_factory=empty_canonical_metadata)
    raw_header: dict[str, Any] = field(default_factory=dict)  # noqa: RUF012 - mutable default via factory


@dataclass(frozen=True, slots=True)
class ImageMetadata:
    """Header-level information without full pixel decode."""

    colorspace: str
    source_colorspace: str
    bit_depth: int
    resolution: tuple[int, int] | None
    metadata: dict[str, Any] = field(default_factory=empty_canonical_metadata)
    raw_header: dict[str, Any] = field(default_factory=dict)  # noqa: RUF012 - mutable default via factory
