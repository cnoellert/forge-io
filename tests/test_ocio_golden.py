"""OCIO transform test using a committed minimal config (no builtin dependency)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("PyOpenColorIO")

from forge_io._color import apply_working_space  # noqa: E402


def _minimal_config_path() -> Path:
    return Path(__file__).resolve().parent / "fixtures" / "ocio" / "minimal.ocio"


def test_apply_working_space_minimal_config_changes_pixels() -> None:
    cfg_path = _minimal_config_path()
    assert cfg_path.is_file(), f"missing committed OCIO fixture: {cfg_path}"

    h, w = 2, 2
    rgb = np.full((h, w, 3), 0.45, dtype=np.float32)
    out, cs = apply_working_space(
        rgb,
        source_colorspace="linear",
        working_space="sRGB - Display",
        assume_source=None,
        ocio_config=cfg_path,
    )
    assert cs == "sRGB - Display"
    assert np.all(np.isfinite(out))
    assert float(np.max(np.abs(out - rgb))) > 1e-3


def test_authoritative_declared_source_ignores_conflicting_assume_source() -> None:
    """A declared colorspace wins over a caller hint (fill-unknown-only).

    The config knows ``linear`` but not ``bogus-hint``. Under override-always the
    hint would be used and OCIO would fail to build the processor; under
    fill-unknown-only the declared ``linear`` is used and the transform succeeds.
    """
    cfg_path = _minimal_config_path()
    rgb = np.full((2, 2, 3), 0.45, dtype=np.float32)
    out, cs = apply_working_space(
        rgb,
        source_colorspace="linear",  # authoritative declared CS
        working_space="sRGB - Display",
        assume_source="bogus-hint",  # conflicting hint — must be ignored
        ocio_config=cfg_path,
    )
    assert cs == "sRGB - Display"
    assert float(np.max(np.abs(out - rgb))) > 1e-3


def test_unknown_declared_filled_by_assume_source() -> None:
    """When the file declares unknown, the hint fills it and the transform runs."""
    cfg_path = _minimal_config_path()
    rgb = np.full((2, 2, 3), 0.45, dtype=np.float32)
    out, cs = apply_working_space(
        rgb,
        source_colorspace="unknown",
        working_space="sRGB - Display",
        assume_source="linear",
        ocio_config=cfg_path,
    )
    assert cs == "sRGB - Display"
    assert float(np.max(np.abs(out - rgb))) > 1e-3
