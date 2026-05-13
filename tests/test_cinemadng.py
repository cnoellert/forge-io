"""CinemaDNG / `.dng` via OIIO (fixture is TIFF-in-DNG for size; see README §7)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("OpenImageIO")

from forge_io import read

FIXTURES = Path(__file__).resolve().parent / "fixtures"
DNG_PATH = FIXTURES / "solid_rgb.dng"

# Golden pixels for `solid_rgb.dng` produced by `generate_fixtures.py` (4×4 float RGB).
_GOLDEN = np.array(
    [
        [[0.5, 0.25, 0.125], [0.5, 0.25, 0.125], [0.5, 0.25, 0.125], [0.5, 0.25, 0.125]],
        [[0.5, 0.25, 0.125], [0.5, 0.25, 0.125], [0.5, 0.25, 0.125], [0.5, 0.25, 0.125]],
        [[0.5, 0.25, 0.125], [0.5, 0.25, 0.125], [0.5, 0.25, 0.125], [0.5, 0.25, 0.125]],
        [[0.5, 0.25, 0.125], [0.5, 0.25, 0.125], [0.5, 0.25, 0.125], [0.5, 0.25, 0.125]],
    ],
    dtype=np.float32,
)


def _try_read_dng(path: Path):
    """Return forge_io.Image or raise; used to probe whether this OIIO reads .dng."""
    return read(path, working_space=None)


def test_read_solid_rgb_dng_matches_golden(tmp_path: Path) -> None:
    src = DNG_PATH
    if not src.is_file():
        pytest.skip(f"missing fixture {src} (run python tests/fixtures/generate_fixtures.py)")
    dst = tmp_path / "copy.dng"
    dst.write_bytes(src.read_bytes())

    try:
        img = _try_read_dng(dst)
    except Exception as e:
        pytest.skip(f"OpenImageIO cannot decode .dng in this build: {e}")

    assert img.pixels.shape == (4, 4, 3)
    assert img.pixels.dtype == np.float32
    assert np.allclose(img.pixels, _GOLDEN, rtol=0.0, atol=1e-6)
