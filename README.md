# forge-io

Shared **read-only** imaging for the Forge stack: **OpenImageIO** decodes pixels into a single predictable shape, with **optional OpenColorIO** transforms when callers opt in. Vendor raw SDKs (ARRI, RED, Sony, CinemaDNG) are planned as separate reader milestones.

## Install

**Use the Forge team Conda environment** (the `forge` env) for local work so OpenImageIO, OpenColorIO, and NumPy match the rest of the Forge stack.

```bash
conda activate forge
cd /path/to/forge-io
pip install -e ".[dev]"
```

If `OpenImageIO` / `opencolorio` are already satisfied in `forge`, `pip` still installs the `dev` extras (`pytest`, `ruff`, etc.) and links this package in editable mode.

The published package depends only on **NumPy**; **`import OpenImageIO`** and **`import PyOpenColorIO`** must work in your environment (Conda, ASWF CI, etc.). **`pip install forge-io` does not install OIIO or OCIO**—you will get `ImportError` on first read if they are missing.

### Versioned installs (downstream deps)

PyPI redistribution is **deferred** (see project policy). Until then, pin a **git tag**:

```bash
pip install "forge-io @ git+https://github.com/cnoellert/forge-io.git@v0.1.0"
```

For private forks, substitute the repo URL; SSH works the same way (`git+ssh://git@github.com/...`). Internal indices (devpi, Artifactory, GitHub Packages) are fine if your org already uses one—this package does not require a specific host.

**Other setups:** a plain `venv` plus `pip install -e ".[dev]"` is fine if wheels exist for your Python version. **CI** uses a pinned ASWF Docker image (see `.github/workflows/ci.yml`), not Conda.

## Public API

- `read(path, *, working_space=None, assume_source=None, ocio_config=None) -> Image`
- `read_frame(pattern, frame_idx, *, ...) -> Image`
- `read_metadata(path) -> ImageMetadata`
- `resolve_pattern(pattern, frame_idx) -> str`

`Image` / `ImageMetadata` carry **`metadata`** (canonical, semver-stable keys, always present; use `None` when a field does not apply) and **`raw_header`** (best-effort OIIO/vendor passthrough; **not** semver-stable).

## Public contract (summary)

### §1 `source_colorspace` — file-declared only

- No filename or path heuristics.
- **v0.1:** only explicit string attributes (e.g. `oiio:ColorSpace`, `colorspace` on the spec) count as “declared.” OpenEXR **chromaticities** blobs are **not** mapped to OCIO roles yet; if you need that, track it as a follow-up.
- If nothing unambiguous is declared in headers → `source_colorspace == "unknown"` (exact lowercase string; no `None`, `""`, or `"Unknown"`).
- `read(..., working_space=...)` with effective source `unknown` and no `assume_source` → **`UnknownColorspaceTransformError`** (OCIO path is not guessed).

### §2 DPX

- **`source_colorspace` is always `"unknown"`** for DPX (header fields are unreliable). Use `assume_source=` when applying `working_space`.

### §3 EXR beauty selection (parts vs layers)

- Walk **subimages** (parts) in order. Within each part:
  1. If unprefixed channels **`R`**, **`G`**, **`B`** exist → beauty candidate for that part.
  2. Else, if exactly one allowlisted layer (`rgba`, `rgb`, `beauty`, case-insensitive layer name) has **`.R` / `.G` / `.B`** → that triple is the candidate.
  3. If more than one allowlisted layer qualifies in the **same** part → **`AmbiguousExrError`**.
- Across the file: **exactly one** `(subimage, triple)` must qualify; otherwise **`AmbiguousExrError`**. Arbitrary AOV layers are ignored unless the layer name is in the allowlist.

### §4 Canonical metadata

Frozen keys: `resolution`, `pixel_aspect`, `timecode`, `framerate` — **always present**; value is **`None`** when the format cannot supply it (e.g. JPEG timecode).

### §5 Readers

- OIIO-backed formats use **`ImageInput` + `spec`** for `read_metadata` / `read_header_only` (no full-frame pixel decode for metadata).

### §6 OCIO config resolution

1. `ocio_config=` argument if passed  
2. else `OCIO` environment variable  
3. else **error** (`OCIOConfigError`)

Where the canonical OCIO config lives (per-project vs machine vs repo) is an **operational** choice for each pipeline; this library only requires that **`OCIO` is set**, **`ocio_config=` is passed**, or callers stay on the decode-only path (`working_space=None`).

### §7 Formats in v0.1

EXR, DPX, PNG, JPEG, TIFF via OIIO.

### §8 CI

GitHub Actions uses a **pinned** [ASWF `ci-vfxall`](https://github.com/AcademySoftwareFoundation/aswf-docker) image so OIIO + OCIO match VFX expectations. Bump the tag deliberately when upgrading.

## Tests

With **`conda activate forge`** (or any env where `OpenImageIO` imports):

```bash
python tests/fixtures/generate_fixtures.py
pytest
```

Contract tests live under `tests/contract/`.

## Semver (library)

- **Patch:** decode/metadata bugfixes preserving the same contract.
- **Minor:** new optional kwargs, new **canonical** keys (additive), new optional readers.
- **Major:** breaking canonical keys or public types/functions.
- **`raw_header` is unstable** across releases.

## Non-goals

No writers, display/view transforms, resize, generic container demux (MOV/MXF via ffmpeg), or catalog persistence — see `forge-io-SKETCH.md`.
