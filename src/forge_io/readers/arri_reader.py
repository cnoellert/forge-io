"""ARRIRAW (``.ari``) reader — scaffold for ARRI Image SDK integration.

This module **claims** ``.ari`` so dispatch is deterministic and callers get a
clear error until the vendor SDK is wired. OpenImageIO is not used for ARRIRAW
here (decode policy lives with the ARRI SDK).

Integration checklist (future commits):
- Detect/bindings for the ARRI Image SDK (system install; not redistributable
  inside the PyPI wheel per ARRI terms).
- ``read_header_only``: resolution, lens/sensor metadata, timecode →
  ``ImageMetadata`` + ``raw_header`` passthrough.
- ``read_pixels``: debayer + matrix to float32 RGB (H, W, 3); populate
  ``source_colorspace`` from declared clip metadata when available, else
  ``unknown`` per forge-io policy.
- Optional: Codex HDE / VRAW in MXF (same SDK family) — separate ``can_read``
  rules once scope is agreed.
"""

from __future__ import annotations

from pathlib import Path

from forge_io._types import ImageMetadata
from forge_io.exceptions import ArriSdkUnavailableError
from forge_io.readers._base import Reader, ReaderDecode

_ARRI_RAW_EXTENSIONS = frozenset({".ari"})

_ARRI_SDK_UNAVAILABLE_MSG = (
    "ARRIRAW (.ari) requires the ARRI Image SDK; decode is not wired in this "
    "build. See forge_io.readers.arri_reader module docstring."
)


def _arri_sdk_available() -> bool:
    """Return True when ARRI SDK Python bindings are linked (not yet implemented)."""
    # Future: try-import official bindings or ctypes-loaded shared library.
    return False


class ArriRawReader(Reader):
    """ARRIRAW container reader (SDK hookup pending)."""

    priority = 5  # before OIIOReader (10) so ``.ari`` never falls through to OIIO

    def can_read(self, path: Path) -> bool:
        return path.suffix.lower() in _ARRI_RAW_EXTENSIONS

    def read_header_only(self, path: Path) -> ImageMetadata:
        p = path.expanduser().resolve()
        if not p.is_file():
            raise FileNotFoundError(str(p))
        if not _arri_sdk_available():
            raise ArriSdkUnavailableError(_ARRI_SDK_UNAVAILABLE_MSG)
        raise NotImplementedError("ARRI SDK is available but read_header_only is not wired")

    def read_pixels(self, path: Path) -> ReaderDecode:
        p = path.expanduser().resolve()
        if not p.is_file():
            raise FileNotFoundError(str(p))
        if not _arri_sdk_available():
            raise ArriSdkUnavailableError(_ARRI_SDK_UNAVAILABLE_MSG)
        raise NotImplementedError("ARRI SDK is available but read_pixels is not wired")
