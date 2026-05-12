"""OCIO transform smoke test using the bundled studio config (when available)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("PyOpenColorIO")

from forge_io._color import apply_working_space  # noqa: E402


def test_apply_working_space_builtin_config_changes_pixels(tmp_path: Path) -> None:
    import PyOpenColorIO as ocio

    try:
        cfg = ocio.Config.CreateFromBuiltinConfig("studio-config-latest")
    except Exception:
        pytest.skip("builtin OCIO config not available")
    cfg_path = tmp_path / "studio.ocio"
    try:
        serialized = cfg.serialize()
    except Exception:
        pytest.skip("Config.serialize not available")
    cfg_path.write_text(serialized, encoding="utf-8")

    # Names exist in ASWF / OCIO studio configs; if they move, update this test.
    src = "Utility - Rec.709 - Display"
    dst = "ACES - ACEScg"
    try:
        cfg.getProcessor(src, dst)
    except Exception:
        pytest.skip(f"no processor {src!r} -> {dst!r} in builtin config")

    h, w = 2, 2
    rgb = np.full((h, w, 3), 0.45, dtype=np.float32)
    out, _cs = apply_working_space(
        rgb,
        source_colorspace=src,
        working_space=dst,
        assume_source=None,
        ocio_config=Path(cfg_path),
    )
    assert np.all(np.isfinite(out))
    assert not np.allclose(out, rgb, rtol=1e-6, atol=1e-6)
