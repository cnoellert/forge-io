from pathlib import Path


class ForgeIOError(Exception):
    """Base error for forge-io."""


class AmbiguousExrError(ForgeIOError):
    """More than one or zero beauty RGB selections match the deterministic EXR rules."""


class UnknownColorspaceTransformError(ForgeIOError):
    """Effective source colorspace is ``unknown`` but a working-space transform was requested."""


class OCIOConfigError(ForgeIOError):
    """OCIO config could not be resolved (missing explicit path and ``OCIO`` env)."""


class OCIOTransformError(ForgeIOError):
    """OCIO failed to build or apply the requested transform."""


class UnsupportedFileError(ForgeIOError):
    """No registered reader accepts this path."""


class ImageDecodeError(ForgeIOError):
    """Pixel decode failed or the file does not match the expected layout."""

    def __init__(self, path: Path | str, message: str) -> None:
        p = Path(path)
        super().__init__(f"{p}: {message}")
        self.path = p
        self.message = message
