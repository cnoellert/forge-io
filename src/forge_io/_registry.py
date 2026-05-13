"""Reader discovery: ordered list, first match wins."""

from __future__ import annotations

from pathlib import Path

from forge_io.exceptions import UnsupportedFileError
from forge_io.readers._base import Reader
from forge_io.readers.arri_reader import ArriRawReader
from forge_io.readers.oiio_reader import OIIOReader
from forge_io.readers.red_reader import RedRawReader

_READERS: list[Reader] = sorted(
    [ArriRawReader(), RedRawReader(), OIIOReader()],
    key=lambda r: r.priority,
)


def get_reader(path: Path) -> Reader:
    for reader in _READERS:
        if reader.can_read(path):
            return reader
    raise UnsupportedFileError(f"No reader registered for path: {path}")
