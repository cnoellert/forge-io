"""Reader protocol."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from forge_io._types import ImageMetadata


@dataclass(frozen=True, slots=True)
class ReaderDecode:
    """Internal decode result before public ``Image`` assembly."""

    pixels: np.ndarray
    source_colorspace: str
    bit_depth: int
    resolution: tuple[int, int]
    metadata: dict[str, Any]
    raw_header: dict[str, Any]


class Reader(ABC):
    """Dispatch by ``can_read``; lower ``priority`` runs first."""

    priority: int = 100

    @abstractmethod
    def can_read(self, path: Path) -> bool: ...

    @abstractmethod
    def read_header_only(self, path: Path) -> ImageMetadata:
        """Must succeed for any path ``can_read`` accepts."""

    @abstractmethod
    def read_pixels(self, path: Path, **opts: Any) -> ReaderDecode:
        """Return float32 RGB (H, W, 3), native range.

        ``opts`` is a free-form dict for per-reader extensions (e.g. RED
        passes ``frame_index`` for intra-clip frame selection). Readers that
        don't recognize a key should ignore it.
        """
