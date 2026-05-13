"""RED R3D (``.r3d``) reader — scaffold for R3D SDK integration.

This module **claims** ``.r3d`` (case-insensitive) so dispatch is deterministic and
callers get a clear error instead of falling through to OpenImageIO. Real decode
is **not** implemented here: it belongs in the **forge-io-red** sibling package
(see `RED_BINDING_PLAN.md` in this repo) built against the `R3D SDK`_.

.. _R3D SDK: https://www.red.com/download/r3d-sdk

SDK / redistribution
----------------------
The R3D SDK is distributed under a RED EULA that requires keeping SDK files in a
**private non-shared directory** and forbids redistributing SDK bits inside
public wheels. forge-io does **not** ship the SDK.

Set ``FORGE_RED_SDK_PATH`` to the absolute path of the SDK shared library (e.g.
``libR3DSDK.dylib`` / ``libR3DAPI.so`` / ``R3DAPI.dll`` — exact name from your
RED install). The gate (``_red_sdk_available``) performs a coarse ``ctypes.CDLL``
load only; symbol / ABI checks belong in **forge-io-red** once decode lands.

Integration checklist (sibling / future):
- Decode path, metadata, timecode, and color pipeline in forge-io-red per
  RED_BINDING_PLAN.md.
"""

from __future__ import annotations

import os
from pathlib import Path

from forge_io._types import ImageMetadata
from forge_io.exceptions import RedSdkUnavailableError
from forge_io.readers._base import Reader, ReaderDecode

_RED_EXTENSIONS = frozenset({".r3d"})

FORGE_RED_SDK_PATH_ENV = "FORGE_RED_SDK_PATH"

_RED_SDK_UNAVAILABLE_MSG = (
    "RED R3D (.r3d) requires the R3D SDK. Set "
    f"{FORGE_RED_SDK_PATH_ENV}=/absolute/path/to/the/SDK/shared-library "
    "(see https://www.red.com/download/r3d-sdk). Real decode is implemented in "
    "the forge-io-red sibling package, not in forge-io core."
)


def _red_sdk_available() -> bool:
    """Coarse gate: ``FORGE_RED_SDK_PATH`` points at a loadable shared library."""
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


class RedRawReader(Reader):
    """R3D container reader (decode lives in forge-io-red sibling)."""

    priority = 6  # after ArriRawReader (5), before OIIOReader (10)

    def can_read(self, path: Path) -> bool:
        return path.suffix.lower() in _RED_EXTENSIONS

    def read_header_only(self, path: Path) -> ImageMetadata:
        p = path.expanduser().resolve()
        if not p.is_file():
            raise FileNotFoundError(str(p))
        if not _red_sdk_available():
            raise RedSdkUnavailableError(_RED_SDK_UNAVAILABLE_MSG)
        raise NotImplementedError("R3D SDK gate open but read_header_only is not wired here")

    def read_pixels(self, path: Path) -> ReaderDecode:
        p = path.expanduser().resolve()
        if not p.is_file():
            raise FileNotFoundError(str(p))
        if not _red_sdk_available():
            raise RedSdkUnavailableError(_RED_SDK_UNAVAILABLE_MSG)
        raise NotImplementedError("R3D SDK gate open but read_pixels is not wired here")
