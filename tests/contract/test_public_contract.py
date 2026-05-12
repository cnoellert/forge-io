"""Public contract tests (plan §1–§4)."""

from __future__ import annotations

import shutil
from pathlib import Path

import numpy as np
import pytest

from forge_io._types import CANONICAL_METADATA_KEYS, UNKNOWN_COLORSPACE, empty_canonical_metadata
from forge_io.exceptions import AmbiguousExrError, UnknownColorspaceTransformError

pytest.importorskip("OpenImageIO")

from forge_io import read, read_metadata  # noqa: E402


def test_unknown_sentinel_is_exact_string() -> None:
    assert UNKNOWN_COLORSPACE == "unknown"


def test_canonical_metadata_keys_always_present() -> None:
    d = empty_canonical_metadata()
    for k in CANONICAL_METADATA_KEYS:
        assert k in d


def test_dpx_source_colorspace_is_unknown(fixtures_dir: Path, tmp_path: Path) -> None:
    src = fixtures_dir / "solid_rgb.dpx"
    if not src.exists():
        pytest.skip("fixtures not generated")
    p = tmp_path / "copy.dpx"
    shutil.copy(src, p)
    img = read(p)
    assert img.source_colorspace == "unknown"
    meta = read_metadata(p)
    assert meta.source_colorspace == "unknown"
    assert meta.bit_depth == 10


def test_working_space_unknown_raises_without_assume(
    fixtures_dir: Path, tmp_path: Path
) -> None:
    src = fixtures_dir / "solid_rgb.dpx"
    if not src.exists():
        pytest.skip("fixtures not generated")
    p = tmp_path / "copy.dpx"
    shutil.copy(src, p)
    with pytest.raises(UnknownColorspaceTransformError):
        read(p, working_space="ACEScg")


def test_exr_ambiguous_two_allowlisted_layers(tmp_path: Path) -> None:
    import OpenImageIO as oiio

    path = tmp_path / "ambiguous.exr"
    w, h = 2, 2
    spec = oiio.ImageSpec(w, h, 6, oiio.FLOAT)
    spec.channelnames = ("rgb.R", "rgb.G", "rgb.B", "beauty.R", "beauty.G", "beauty.B")
    buf = oiio.ImageBuf(spec)
    arr = np.zeros((h, w, 6), dtype=np.float32)
    buf.set_pixels(oiio.ROI(0, w, 0, h, 0, 1, 0, 6), arr)
    assert buf.write(str(path))
    with pytest.raises(AmbiguousExrError):
        read(path)


def test_exr_happy_path_and_metadata_no_decode_pixels(fixtures_dir: Path, tmp_path: Path) -> None:
    src = fixtures_dir / "solid_rgb.exr"
    if not src.exists():
        pytest.skip("fixtures not generated")
    p = tmp_path / "copy.exr"
    shutil.copy(src, p)
    img = read(p)
    assert img.source_colorspace == "unknown"
    assert img.pixels.shape == (4, 4, 3)
    assert img.pixels.dtype == np.float32
    np.testing.assert_allclose(img.pixels[..., 0], 0.5, rtol=1e-5)
    np.testing.assert_allclose(img.pixels[..., 1], 0.25, rtol=1e-5)
    np.testing.assert_allclose(img.pixels[..., 2], 0.125, rtol=1e-5)
    meta = read_metadata(p)
    assert meta.source_colorspace == "unknown"
    assert meta.resolution == (4, 4)
    assert meta.bit_depth == 32
