# forge-io — sketch

Shared image-read library for the Forge ecosystem. Wraps OIIO, OCIO, and vendor raw SDKs behind one API so every Forge tool reads pixels the same way.

Status: **v0.1 library** — code lives in `src/forge_io/`; this document remains the product sketch and may lag README for low-level contracts.

---

## Mission

Read pixels from disk and return them in a predictable shape, with optional colorspace transform applied via OCIO.

That's it.

## Non-goals

- **No writers.** Acquisition formats aren't delivery formats; nobody downstream wants ARRIRAW out.
- **No display transforms.** View LUTs, exposure, creative color → different library.
- **No resize / processing / preview.** Reader returns what's on disk.
- **No container extraction.** MOV/MP4/MXF → frames is a transcode step (lives elsewhere, calls ffmpeg).
- **No catalog / metadata persistence.** That's traffik's job. forge-io exposes what's in the file header; traffik stores it.

If a feature request doesn't reduce to "read pixels," it doesn't belong here.

---

## API surface

### Primary read

```python
import forge_io

# Default: native pixels + colorspace metadata. No OCIO touched.
img = forge_io.read("shot.0042.exr")
# img.pixels      → np.ndarray, float32, (H, W, 3), channels-last, native range
# img.colorspace  → "ACEScg" (or declared source; see README for file-declared-only policy)
# img.bit_depth   → 32  (inferred from source dtype, not post-load)
# img.metadata    → dict of header attrs (timecode, framerate, custom EXR attrs, etc.)

# Opt-in: caller declares working space; OCIO transform applied on the way out.
img = forge_io.read("shot.0042.exr", working_space="ACEScg")
# img.pixels      → transformed into ACEScg
# img.colorspace  → "ACEScg" (now reflects current state, not source)
```

### Sequence pattern resolution

Pulled out of forge-align's `extractor.py:27`. First-class helper, separate from the reader so callers can resolve once and read many.

```python
path = forge_io.resolve_pattern("shot.[0001-0200].exr", frame_idx=42)
# → "shot.0042.exr"

# Supports: printf (%04d), Flame brackets ([0001-0200]), literal paths.
```

### Convenience: pattern + read in one call

```python
img = forge_io.read_frame("shot.[0001-0200].exr", frame_idx=42, working_space="ACEScg")
```

### Metadata-only read (cheap)

For traffik-style consumers that want header info without loading pixels.

```python
meta = forge_io.read_metadata("shot.0042.exr")
# → ImageMetadata(colorspace=..., bit_depth=..., timecode=..., resolution=..., ...)
```

---

## Return type

```python
@dataclass
class Image:
    pixels: np.ndarray          # float32, (H, W, C), channels-last, RGB order
    colorspace: str             # current colorspace, post-transform if applied
    source_colorspace: str      # file-declared only; otherwise "unknown" (exact string)
    bit_depth: int              # source bit depth (8, 10, 12, 16, 32)
    resolution: tuple[int, int] # (width, height)
    metadata: dict              # canonical keys only (always present; None if absent)
    raw_header: dict            # best-effort OIIO/vendor attrs (not semver-stable)
```

**Pixel contract:**
- `float32`, `(H, W, C)`, channels-last, **RGB** (not BGR — we cvtColor on the way out, OpenCV-style BGR is a footgun).
- Default range: native to the colorspace (linear scene-referred for EXR, code-value for DPX/log, [0,1] for sRGB display-referred). **Not** auto-normalized to [0,1] unless caller asks for a working space that implies it.

This is a deliberate change from forge-align's current "always [0,1]" behavior — it pushes that decision to the caller, where it belongs.

---

## Colorspace handling — opt-in, never automatic

Three modes:

| Caller passes | Behavior |
|---|---|
| nothing | Return native pixels + report `source_colorspace`. No OCIO touched. |
| `working_space="ACEScg"` | OCIO transform from `source_colorspace` to `ACEScg` applied. |
| `working_space="ACEScg"`, `assume_source="ARRI LogC4 / AWG4"` | Override source detection. For files with bad/missing metadata. |

**OCIO config resolution order:**
1. Explicit `ocio_config=...` argument
2. `OCIO` environment variable
3. **Error.** Never guess.

This eliminates forge-align's hand-rolled `_linear_to_srgb()` / `_log_to_srgb()` (`solver.py:348-379`), which are approximations that drift from reference.

---

## Format support — phased

**Phase 1 (day-one, matches actual forge-align usage):**
- EXR (via OIIO)
- DPX (via OIIO; `source_colorspace` is always `"unknown"` — use `assume_source` for OCIO)
- PNG, JPEG, TIFF (via OIIO)

**Phase 2 (when a real consumer needs it):**
- ARRIRAW `.ari` (via ARRI SDK)
- Codex VRAW MXF (via ARRI SDK)

**Phase 3 (only if pipeline demands it):**
- R3D (RED SDK)
- BRAW (Blackmagic SDK)
- Sony X-OCN (Sony SDK)

Don't build phases 2-3 speculatively. Each vendor SDK comes with redistribution headaches; only pay that cost when there's a real consumer.

---

## Package layout

```
forge-io/
├── pyproject.toml
├── README.md
├── src/forge_io/
│   ├── __init__.py              # public API: read, read_frame, read_metadata, resolve_pattern
│   ├── _types.py                # Image, ImageMetadata dataclasses
│   ├── _registry.py             # format → reader dispatch
│   ├── _patterns.py             # printf / Flame bracket / literal resolution
│   ├── _color.py                # OCIO config resolution + transform
│   ├── readers/
│   │   ├── _base.py             # Reader protocol
│   │   ├── oiio_reader.py       # EXR, DPX, TIFF, PNG, JPEG via OIIO
│   │   └── arri_reader.py       # (phase 2) ARRIRAW via ARRI SDK
│   └── py.typed
└── tests/
    ├── fixtures/                # tiny test images per format
    └── ...
```

Public API surface is **four functions**: `read`, `read_frame`, `read_metadata`, `resolve_pattern`. Everything else is internal.

---

## Dependencies

**Required:**
- `numpy`
- `OpenImageIO` (Python bindings)
- `PyOpenColorIO`

**Optional / phase 2:**
- ARRI SDK (system install + Python bindings or ctypes wrapper)

**Explicitly not depended on:**
- `opencv-python` — we don't need it for reads; OIIO is better.
- `imageio` — overlapping scope.
- `Pillow` — OIIO covers JPEG/PNG.
- `ffmpeg` — container extraction is out of scope.

---

## Migration path for forge-align

The audit found two read sites in forge-align (`extractor.py:42`, `extractor.py:115`) plus the colorspace-classification block in `solver.py:282-379`.

Migration is roughly:

1. Replace `cv2.imread(...)` + manual normalization → `forge_io.read(path)` (returns float32 RGB already, no BGR swap, no /255 / /65535 needed).
2. Delete `_linear_to_srgb` / `_log_to_srgb` / `_classify_colorspace` (~100 lines of `solver.py`). Replace with `forge_io.read(path, working_space="<solver_working_space>")`.
3. Move sequence pattern resolution (`_resolve_sequence_path` in extractor.py) → call `forge_io.resolve_pattern` instead.
4. Container extraction (`extract_container_frame`, `extractor.py:63`) **stays in forge-align** for now — it's transcode, not read. Could move to a `forge-transcode` later if a second consumer needs it.

Net delta in forge-align: ~150 lines deleted, ~30 lines changed, behavior more correct (real OCIO instead of approximation).

---

## Open questions

1. **OCIO config — where does it live?** Per-project? Per-machine? Forge needs to decide where the canonical config is and how tools find it. forge-io enforces explicit-or-env-var, but the *operational* answer is upstream of this library.

2. **ARRI SDK redistribution.** Internal use almost certainly fine. If forge-io is ever published externally (PyPI, GitHub public), the ARRI SDK can't ship in the wheel — it'd need a build-time install step. Don't have to solve this until phase 2.

3. **Channel count beyond RGB.** EXR multi-part / multi-channel (deep, AOVs, ID mattes) — does forge-io expose them, or only the RGB beauty? Day-one: beauty only. Multi-channel is a separate API (`read_channels`, `read_part`) when a consumer needs it.

4. **Async / streaming.** For sequence reads, do we want `read_sequence(pattern, range) → iterator`? Useful for ML pipelines that don't want to load 200 frames into RAM. Probably yes, but not day-one.

5. **Caching.** Does forge-io cache decoded frames? Probably no — that's a consumer concern. But worth being explicit about it.

6. **Thread safety.** OIIO is thread-safe; OCIO transforms are thread-safe per processor. Document the contract.

---

## What "done" looks like for v0.1

- `read()`, `read_frame()`, `read_metadata()`, `resolve_pattern()` work for EXR + DPX + PNG + JPEG + TIFF (OIIO path).
- **This repo:** contract tests + fixtures + pinned CI (ASWF image) + README public contract + `pyproject.toml` / wheel build.
- **Integration (separate milestone):** forge-align migrated to use forge-io for all reads (~150 lines deleted there) once consumers are ready.
- OCIO transform path covered by tests where a config is available (ACES-style configs in CI).

That's it for v0.1 in **forge-io**. Phase 2 (ARRIRAW) is its own milestone with its own scope conversation.
