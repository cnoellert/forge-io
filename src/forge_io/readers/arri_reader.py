"""ARRIRAW (``.ari``) reader — scaffold for ARRI Image SDK integration.

This module **claims** ``.ari`` so dispatch is deterministic and callers get a
clear error until the vendor SDK is wired. OpenImageIO is not used for ARRIRAW
here (decode policy lives with the ARRI SDK).

SDK discovery
-------------
forge-io does **not** redistribute the ARRI Image SDK (Partner Program
distribution, EULA forbids derivative redistribution). To enable ARRIRAW
decode, set ``FORGE_ARRI_SDK_PATH`` to the absolute path of the SDK shared
library (e.g. ``libArriImageSdk.dylib`` on macOS, ``libArriImageSdk.so`` on
Linux — the actual filename comes from your Partner Program install).

The gate (``_arri_sdk_available``) does a coarse ``ctypes.CDLL`` load only;
it does not verify symbols or API version. The eventual SDK adapter is
responsible for ABI / version checks once decode is wired.

Integration checklist (future commits):
- ``read_header_only``: resolution, lens/sensor metadata, timecode →
  ``ImageMetadata`` + ``raw_header`` passthrough.
- ``read_pixels``: debayer + matrix to float32 RGB (H, W, 3); populate
  ``source_colorspace`` from declared clip metadata when available, else
  ``unknown`` per forge-io policy.
- Codex HDE / RDD 54/55 MXF: handled by a sibling ``arri_mxf_reader.py``
  that links the ARRI MXF Library in addition to the Image SDK.
"""

from __future__ import annotations

import os
from pathlib import Path

from forge_io._types import ImageMetadata
from forge_io.exceptions import ArriSdkUnavailableError
from forge_io.readers._base import Reader, ReaderDecode

_ARRI_RAW_EXTENSIONS = frozenset({".ari"})

FORGE_ARRI_SDK_PATH_ENV = "FORGE_ARRI_SDK_PATH"

_ARRI_SDK_UNAVAILABLE_MSG = (
    "ARRIRAW (.ari) requires the ARRI Image SDK. Set "
    f"{FORGE_ARRI_SDK_PATH_ENV}=/absolute/path/to/libArriImageSdk.<so|dylib|dll> "
    "to enable. The SDK is distributed only via the ARRI Camera Partner "
    "Program and cannot be redistributed by forge-io."
)


def _arri_sdk_available() -> bool:
    """Coarse gate: ``FORGE_ARRI_SDK_PATH`` points at a loadable shared library.

    Returns ``False`` when the env var is unset, points at a non-file, or
    fails to load via ``ctypes.CDLL``. Symbol-level verification is the
    SDK adapter's responsibility once decode is wired.
    """
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
