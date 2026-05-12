"""Generate tiny binary fixtures (requires OpenImageIO).

Run: ``python tests/fixtures/generate_fixtures.py``
"""

from __future__ import annotations

import sys
from pathlib import Path

OUT = Path(__file__).resolve().parent


def main() -> None:
    import numpy as np
    import OpenImageIO as oiio

    h, w = 4, 4
    spec = oiio.ImageSpec(w, h, 3, oiio.FLOAT)
    spec.channelnames = ("R", "G", "B")
    buf = oiio.ImageBuf(spec)
    arr = np.zeros((h, w, 3), dtype=np.float32)
    arr[..., 0] = 0.5
    arr[..., 1] = 0.25
    arr[..., 2] = 0.125
    buf.set_pixels(oiio.ROI(0, w, 0, h, 0, 1, 0, 3), arr)
    exr_path = OUT / "solid_rgb.exr"
    buf.write(str(exr_path))

    # DPX: UINT16 container with declared 10-bit samples (policy: colorspace still unknown)
    dpx_spec = oiio.ImageSpec(w, h, 3, oiio.UINT16)
    dpx_spec.attribute("oiio:ColorSpace", "rec709")  # ignored for DPX policy
    dpx_spec.attribute("oiio:BitsPerSample", 10)
    dpx = oiio.ImageBuf(dpx_spec)
    dpx.set_pixels(oiio.ROI(0, w, 0, h, 0, 1, 0, 3), (np.clip(arr, 0, 1) * 65535).astype(np.uint16))
    dpx_path = OUT / "solid_rgb.dpx"
    dpx.write(str(dpx_path))

    png_spec = oiio.ImageSpec(w, h, 3, oiio.UINT8)
    png_spec.channelnames = ("R", "G", "B")
    png = oiio.ImageBuf(png_spec)
    png.set_pixels(oiio.ROI(0, w, 0, h, 0, 1, 0, 3), (arr * 255).astype(np.uint8))
    png_path = OUT / "solid_rgb.png"
    png.write(str(png_path))

    print("Wrote:", exr_path, dpx_path, png_path, file=sys.stderr)


if __name__ == "__main__":
    main()
