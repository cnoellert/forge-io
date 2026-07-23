"""OCIO config resolution and CPU transforms."""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np

from forge_io._types import UNKNOWN_COLORSPACE
from forge_io.exceptions import OCIOConfigError, OCIOTransformError, UnknownColorspaceTransformError


def resolve_ocio_config_path(explicit: str | Path | None) -> str:
    if explicit is not None:
        return str(Path(explicit).expanduser().resolve())
    env = os.environ.get("OCIO")
    if env:
        return str(Path(env).expanduser().resolve())
    raise OCIOConfigError(
        "No OCIO config: pass ocio_config=... or set the OCIO environment variable "
        "to a config path."
    )


def effective_source_colorspace(
    declared: str,
    assume_source: str | None,
) -> str:
    """Resolve the colorspace to transform from — authoritative decode wins.

    ``assume_source`` is **fill-unknown-only**: a reader that declares a real
    ``source_colorspace`` (raw → ``ACES2065-1`` / ``Linear REDWideGamutRGB``;
    OIIO from embedded metadata) is never second-guessed by a caller hint. The
    hint only supplies the colorspace when the file itself declares
    ``unknown``. When it stays ``unknown``, :func:`apply_working_space` raises
    ``UnknownColorspaceTransformError`` with guidance.

    Forcing a reinterpretation of a file that declares a real (possibly
    mis-tagged) colorspace is intentionally *not* supported via
    ``assume_source`` — that belongs in an explicit, loud opt-in
    (``force_source_colorspace=``), not a silent side-effect of a hint.
    """
    if declared != UNKNOWN_COLORSPACE:
        return declared
    if assume_source is not None and assume_source != UNKNOWN_COLORSPACE:
        return assume_source
    return declared


def apply_working_space(
    pixels_hwc: np.ndarray,
    *,
    source_colorspace: str,
    working_space: str,
    assume_source: str | None,
    ocio_config: str | Path | None,
) -> tuple[np.ndarray, str]:
    """Return (transformed_pixels, output_colorspace_name)."""
    src = effective_source_colorspace(source_colorspace, assume_source)
    if src == UNKNOWN_COLORSPACE:
        raise UnknownColorspaceTransformError(
            f"Cannot transform from colorspace {UNKNOWN_COLORSPACE!r}: pass assume_source=... "
            "with the file's encoding."
        )
    import PyOpenColorIO as ocio

    cfg_path = resolve_ocio_config_path(ocio_config)
    try:
        config = ocio.Config.CreateFromFile(cfg_path)
    except Exception as e:  # noqa: BLE001 - OCIO raises various types
        raise OCIOConfigError(f"Failed to load OCIO config {cfg_path!r}: {e}") from e
    try:
        proc = config.getProcessor(src, working_space)
        cpu = proc.getDefaultCPUProcessor()
    except Exception as e:
        raise OCIOTransformError(
            f"Failed to build OCIO processor {src!r} -> {working_space!r}: {e}"
        ) from e

    h, w, c = pixels_hwc.shape
    if c != 3:
        raise OCIOTransformError(f"OCIO path expects 3 channels, got {c}")
    out = np.ascontiguousarray(pixels_hwc, dtype=np.float32).copy()
    flat = out.reshape(-1, 3)
    # Vectorized apply: PyOpenColorIO CPUProcessor.applyRGB works on list-like length-3.
    # For large buffers use PackedImageDesc when available.
    try:
        desc = ocio.PackedImageDesc(out, w, h, 3)
        cpu.apply(desc)
    except Exception:
        for i in range(flat.shape[0]):
            rgb = list(flat[i])
            cpu.applyRGB(rgb)
            flat[i] = rgb
    return out, working_space
