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
pip install "forge-io @ git+https://github.com/cnoellert/forge-io.git@v0.3.2"
```

**v0.3.x highlights:**
- `v0.3.0` — RED `.r3d` decode via REDline subprocess backend.
- `v0.3.1` — Per-frame R3D selection: `read(path, frame_index=N)` forwards to REDline `--start N --end N`. Reader protocol gained `**opts` plumbing.
- `v0.3.2` — Reader-emitted `source_colorspace` strings switched to OCIO-canonical names: ARRI → `ACES2065-1`, RED → `Linear REDWideGamutRGB`. Decoded pixels unchanged; transforms now resolve in real-world OCIO configs without an `assume_source` translation table.

For private forks, substitute the repo URL; SSH works the same way (`git+ssh://git@github.com/...`). Internal indices (devpi, Artifactory, GitHub Packages) are fine if your org already uses one—this package does not require a specific host.

**Other setups:** a plain `venv` plus `pip install -e ".[dev]"` is fine if wheels exist for your Python version. **CI** uses a pinned ASWF Docker image (see `.github/workflows/ci.yml`), not Conda.

## Public API

- `read(path, *, working_space=None, assume_source=None, ocio_config=None, frame_index=0) -> Image`
- `read_frame(pattern, frame_idx, *, working_space=None, assume_source=None, ocio_config=None) -> Image`
- `read_metadata(path) -> ImageMetadata`
- `resolve_pattern(pattern, frame_idx) -> str`

`read()` operates on single files. `frame_index` (v0.3.1+) is the 0-based intra-clip frame for single-file raw clips that contain multiple frames internally (e.g. RED `.r3d` → forwarded to REDline `--start N --end N`); readers that don't support intra-clip selection (OIIO, ARRI single-frame `.ari/.arx`) ignore it.

`read_frame()` is for image sequences — it resolves `pattern + frame_idx` to a path via `resolve_pattern` and calls `read()`. Use it for EXR/DPX/PNG sequences and for **ARRI `.ari/.arx`** (which are one-file-per-frame on disk despite being raw — the filename carries the frame number).

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

### §7 Formats

EXR, DPX, PNG, JPEG, TIFF via OIIO. ARRIRAW + RED R3D via vendor backends (subprocess today, pybind11 future).

**ARRIRAW (`.ari` and HDE-compressed `.arx`):** a reader is **registered** (before OIIO in the dispatch list) with **two backends**:

1. **ARRI Image SDK (pybind11)** — `FORGE_ARRI_SDK_PATH` points at the SDK shared library; real decode lives in the eventual **forge-io-arri** sibling package. **Pending Partner Program SDK access** — the gate here is currently a coarse `ctypes.CDLL` load only.

2. **ART-CMD subprocess** — `FORGE_ARRI_ART_PATH` points at the `art-cmd` binary from [ARRI Reference Tools](https://www.arri.com/en/learn-help/learn-help-camera-system/tools/arri-reference-tool). forge-io shells out per call: decodes one frame to ACES AP0 scene-linear EXR (ART-CMD is invoked with `--target-colorspace AP0/D60/linear --video-codec exr_uncompressed/f16 --render-platform cpu` for determinism), reads it back via OIIO, returns **`source_colorspace="ACES2065-1"`** (v0.3.2+ — the OCIO-canonical name for AP0 primaries + D60 white + linear transfer; pre-v0.3.2 used the literal ART-CMD string `"AP0/D60/linear"`). Downstream OCIO transforms operate on that known intermediate. `read_metadata` uses `art-cmd export --skip-audio --skip-look` for header-only reads (no pixel decode).

When **both** backends are configured, the SDK path takes precedence (faster, no disk roundtrip). When **neither** is configured, `ArriSdkUnavailableError` names both env vars so the user knows their options.

**Sequence semantics:** `.ari/.arx` are **one file per frame on disk** (the frame number is in the filename), so callers use `read_frame(pattern, frame_idx)` — or `read(specific_frame_path)` directly. ART-CMD's `--start N` is offset within the clip; forge-io scans the clip directory to find the lowest frame index and computes the offset internally so callers always pass the absolute frame number they want. The `frame_index` kwarg on `read()` is **not** used for ARRI (it's for single-file multi-frame clips like R3D).

**macOS gotcha:** Safari-downloaded ART-CMD bundles are quarantined; if `art-cmd` fails to load its dylibs with "code signature ... not valid", clear the quarantine:

```bash
xattr -dr com.apple.quarantine /Applications/art-cmd_*
```

forge-io does **not** redistribute either the ARRI Image SDK or ART-CMD. The SDK is gated behind the [ARRI Camera Partner Program](https://www.arri.com/en/company/the-arri-philosophy/camera-partner-program); ART-CMD is a free download under its own EULA (which permits subprocess invocation by third-party tools — no copying / modification / transfer required).

**RED R3D (`.r3d`):** a reader is **registered** (after ARRI, before OIIO) with **two backends**:

1. **R3D SDK pybind11** — `FORGE_RED_SDK_PATH` points at the SDK shared library; real decode lives in the eventual **forge-io-red** sibling package (see [`RED_BINDING_PLAN.md`](RED_BINDING_PLAN.md)). **Pending RED Developer Program SDK access** — the gate here is currently a coarse `ctypes.CDLL` load only. Note: the `REDR3D.dylib` shipped inside consumer host apps (REDCINE-X, DaVinci Resolve, Nuke, Mocha, Fusion, SynthEyes, BLG) is symbol-stripped — the coarse gate would pass but the eventual sibling will fail to bind. Point this env var at the actual SDK download, not a host-app dylib.

2. **REDline subprocess** — `FORGE_RED_REDLINE_PATH` points at the `REDline` binary bundled with [REDCINE-X PRO](https://www.red.com/downloads) (free download). On macOS the path is typically `/Applications/REDCINE-X Professional/REDCINE-X PRO.app/Contents/MacOS/REDline`. forge-io shells out per call: decodes one frame to REDWideGamutRGB scene-linear half-float EXR (REDline is invoked with `--format 2 --res 1 --colorSpace 25 --gammaCurve -1 --useMeta --start N --end N`), reads it back via OIIO, returns **`source_colorspace="Linear REDWideGamutRGB"`** (v0.3.2+ — OCIO 2.x studio-config canonical name; pre-v0.3.2 used `"REDWideGamutRGB/linear"`). Downstream OCIO transforms operate on that known intermediate. `read_metadata` uses `--printMeta 1` for header-only reads (no pixel decode).

**Per-frame selection (v0.3.1+):** `.r3d` is a single-file multi-frame clip. Pass `read(path, frame_index=N)` (or via downstream wrappers) to select the 0-based intra-clip frame; forge-io forwards N as REDline `--start N --end N`. Negative values raise `ValueError`; out-of-range values surface as `ImageDecodeError` via REDline's own rejection. Pre-v0.3.1 always decoded frame 0.

**Facility OCIO note:** `Linear REDWideGamutRGB` is the OCIO 2.x studio-config canonical name, but most Flame-bundled OCIO configs (`flame_core_config`, `aces2.0_config`) **do not ship that colorspace** as of Flame 2026.0. For OCIO transforms `Linear REDWideGamutRGB → working_space` to resolve, add the colorspace to your facility's `project_custom_config.ocio` overlay (real RWG → ACES2065-1 math) or alias it to `ACEScg` for a CV-acceptable approximation (small gamut shift, transfer is correct). ARRI's emitted `ACES2065-1` is in every ACES-shipped config.

When **both** backends are configured, the SDK path takes precedence. When **neither** is configured, `RedSdkUnavailableError` names both env vars so the user knows their options.

**Decode contract:** the REDline backend pins output to REDWideGamutRGB primaries + linear transfer regardless of the source clip's color science. For IPP2 clips this is the natural default; for Legacy clips REDline applies its internal Legacy→IPP2 transform. forge-io trusts RED's authoritative color science here, paralleling the ARRI ART-CMD backend's `ACES2065-1` posture — both readers land downstream OCIO on a known wide-gamut linear intermediate.

forge-io does **not** redistribute the R3D SDK or REDline. The SDK is gated behind the [RED Developer Program](https://www.red.com/developers); REDline is bundled inside the free REDCINE-X PRO download under its EULA (which permits subprocess invocation by third-party tools — no copying / modification / transfer required).

**Sony X-OCN:** forge-io does **not** register an `.mxf` reader. X-OCN decode requires [Sony Partner Program](https://pro.sony) SDK access (NDA-gated) or a facility-licensed partner path (e.g. nablet AMA). There is no bundled Sony decode here — use **Sony RAW Viewer's RAW Exporter** (or similar) to transcode X-OCN to EXR/DPX upstream, then read those formats with OIIO. The public type **`SonyUnsupportedError`** documents this policy for downstream code that may raise it when a future optional path is absent.

**CinemaDNG (`.dng`):** forge-io routes `.dng` through **`OIIOReader`** → OpenImageIO → **LibRaw** (or the Adobe DNG SDK path) when the build includes a raw/DNG input plugin. ASWF **`ci-vfxall`** images used in CI ship that stack. **LibRaw version bumps can change decoded pixels** for real camera Bayer DNGs; pin versions in production and consider **[`rawtoaces`](https://github.com/AcademySoftwareFoundation/rawtoaces)** for ACES pipelines. The committed test fixture **`solid_rgb.dng`** is intentionally a **small TIFF float RGB bitstream** under a `.dng` suffix (≤ 1 KiB) so CI stays deterministic; it does **not** exercise LibRaw demosaic variance — add a separate golden when a tiny Bayer sample is available.

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
