# Changelog

## Unreleased

## v0.4.0

**Editorial/delivery container reader (ffmpeg) — `.mov` / `.mp4` / `.m4v` / `.avi` / `.mkv`.**

`forge_io.read()` and `read_metadata()` now decode common editorial and delivery containers (QuickTime ProRes / H.264 `.mov`, `.mp4`, etc.). Previously these fell through to `UnsupportedFileError`; consumers (forge-align, forge-flow) worked around it by shelling `ffmpeg` themselves. That decode now lives in forge-io so every consumer gets it with consistent colorspace + metadata handling.

- **New `FFmpegReader`** (`priority=8`, after camera-raw ARRI/RED, before OIIO). Registers `.mov`, `.mp4`, `.m4v`, `.avi`, `.mkv`.
- **Backend:** a single `ffmpeg` subprocess decodes one frame to a 16-bit RGB PNG in a temp dir, read back via OIIO → `float32 (H, W, 3)`. `ffprobe` supplies header metadata (no pixel decode for `read_metadata`). forge-io does **not** bundle ffmpeg — both binaries are discovered on `PATH`, or via `FORGE_FFMPEG_PATH` / `FORGE_FFPROBE_PATH`; if neither is found, the new **`FFmpegUnavailableError`** names the options.
- **Frame-accurate seeking:** `read(path, frame_index=N)` selects frame N by **frame number** (`-vf select='gte(n\,N)' -frames:v 1`, decode-from-head), which is exact for long-GOP codecs (H.264/H.265). This deliberately avoids input-side time seeking (`-ss` before `-i` computed from a caller-supplied fps), which can land ±1 frame off on fractional rates or when the assumed fps is wrong. Negative `frame_index` raises `ValueError`.
- **Colorspace posture:** editorial containers report **`source_colorspace="unknown"`** — the same posture `OIIOReader` takes for PNG/JPEG/DPX. forge-io does not infer a colorspace from container tags or filenames; callers apply `working_space` with an explicit `assume_source`. The raw ffprobe color tags (`color_primaries` / `color_transfer` / `color_space` / `color_range`) are preserved in `raw_header`.
- **Metadata:** canonical `resolution`, `framerate` (from `r_frame_rate` rational), `pixel_aspect` (SAR), and `timecode` (stream then container tag) are populated. Source bit depth is reported from `bits_per_raw_sample` (else inferred from `pix_fmt`, else 8). Frame count + fps + codec/pixel-format + color tags are stashed in `raw_header`.
- **`.mxf` is intentionally not registered.** It cannot be disambiguated at the extension level between editorial MXF (DNxHD/XDCAM, ffmpeg-decodable) and Sony X-OCN raw (not ffmpeg-decodable), and forge-io's Sony policy keeps `.mxf` unregistered so X-OCN raises `UnsupportedFileError` (see README §7). Editorial-MXF support is a future item pending that reconciliation.
- New public export: `FFmpegUnavailableError`. Live e2e tests synthesize clips with ffmpeg (ffv1 lossless exactness, ffprobe metadata, libx264 long-GOP frame accuracy) and run whenever ffmpeg is on `PATH`.

Non-breaking: no change to existing OIIO/ARRI/RED reads or the public `read()` / `read_metadata()` signatures.

## v0.3.2

**OCIO-canonical source_colorspace names for ARRI and RED.**

Decoders still produce the same pixels, but the reported `source_colorspace` string now matches names that real-world OCIO configs actually use, so downstream callers no longer need an `assume_source` translation table to make `working_space=` transforms resolve.

- `ArriRawReader.source_colorspace`: `"AP0/D60/linear"` → **`"ACES2065-1"`** (OCIO-canonical for AP0 primaries + D60 white + linear transfer — exactly what ART-CMD decodes to). The ART-CMD argument string (`--target-colorspace AP0/D60/linear`) is unchanged; only the public reporting name changed.
- `RedRawReader.source_colorspace`: `"REDWideGamutRGB/linear"` → **`"Linear REDWideGamutRGB"`** (OCIO 2.x studio-config canonical name). REDline CLI codes (`--colorSpace 25 --gammaCurve -1`) are unchanged.
- Live and unit tests updated. Other reader internals untouched.

Caveat for RED: most Flame-bundled OCIO configs (including `flame_core_config` and `aces2.0_config` as of 2026.0) do not yet ship a `Linear REDWideGamutRGB` colorspace. Facilities using forge-io for RED reads should add one to their `project_custom_config.ocio` overlay, or alias it to `ACEScg` for a CV-acceptable approximation if exact color isn't required.

Breaking for callers that assert on the literal old strings; non-breaking for any caller that feeds `source_colorspace` directly to OCIO (it now resolves where it didn't before).

## v0.3.1

**RED R3D per-frame decode.**

- `read()` gains `frame_index: int = 0` — forwarded to the active reader via the new `**opts` plumbing on `Reader.read_pixels`. OIIO and ARRI readers ignore it; `RedRawReader` consumes it.
- `_decode_via_redline` now takes `frame_index` and renders REDline's `--start N --end N` from it, replacing the hardcoded `--start 0 --end 0` of v0.3.0. Negative values raise `ValueError`. Out-of-range values surface as REDline subprocess failures via `ImageDecodeError` (REDline rejects them).
- Reader Protocol: `Reader.read_pixels(self, path, **opts)` is the new contract. Subclasses that don't recognize a key should ignore it. Backwards-compatible for callers using only `read()` / `read_frame()` / `read_metadata()`.
- For image sequences continue using `read_frame(pattern, frame_idx, ...)` (resolves a pattern + frame number to a path). `frame_index` on `read()` is for single-file clip semantics.

## v0.3.0

**RED R3D decode via REDline subprocess backend.**

- `RedRawReader` now has **two backends**, mirroring the ARRI v0.2.1 pattern:
  - **Option 1 (scaffold):** `FORGE_RED_SDK_PATH` — coarse `ctypes.CDLL` gate, real decode pending the `forge-io-red` sibling per `RED_BINDING_PLAN.md`.
  - **Option 2 (working):** `FORGE_RED_REDLINE_PATH` points at the `REDline` binary bundled with REDCINE-X PRO. forge-io shells out per call, decoding one frame to REDWideGamutRGB scene-linear half-float EXR (`--format 2 --res 1 --colorSpace 25 --gammaCurve -1 --useMeta`), reads it back via OIIO, returns `source_colorspace="REDWideGamutRGB/linear"`.
- Backend selection: SDK gate (option 1) takes precedence when configured; REDline (option 2) is the fallback; `RedSdkUnavailableError` names both env vars when neither is set.
- `read_metadata` for `.r3d` uses `--printMeta 1` for header-only reads (no pixel decode, ~0.7s wall). Canonical fields (resolution, framerate, timecode, pixel_aspect) parsed from REDline's `Key:\tValue` output; full field dump retained under `raw_header["redline_meta"]`. REDline returns exit code 1 for metadata-only invocations — `_run_redline` accepts that explicitly via `accept_codes=(0, 1)`.
- `read` path uses a single REDline invocation; canonical metadata is harvested from the EXR's `extra_attribs` REDline forwards (`FrameWidth`, `FrameHeight`, EXR-standard `framesPerSecond` rational, `PixelAspectRatio`, `TOD TC Start`). Decode pins primaries + transfer regardless of source IPP2/Legacy — trusts RED's authoritative color science, parallels ARRI's `AP0/D60/linear` posture.
- Decode contract verified live against an 8192×4320 V-RAPTOR XE IPP2 clip: full-res half-float EXR in ~1.34s wall (cold) on Apple Silicon; canonical metadata: resolution `(8192, 4320)`, framerate `24000/1001` from EXR rational, timecode from TOD TC.
- Empirically confirmed: `REDR3D.dylib` shipped inside every consumer host app (REDCINE-X, Resolve, Nuke, Mocha, Fusion, SynthEyes, BLG) is symbol-stripped — only 179–195 obfuscated `R3D_AAA` trampolines export, zero `R3DSDK::*`. The SDK gate's coarse `ctypes.CDLL` would false-positive on those; documented in `red_reader.py` and README §7.
- `RED_BINDING_PLAN.md` re-framed: subprocess path ships now, pybind11/SDK shim is future `forge-io-red` work for lower decode latency. Plan also gained §6 with the 133-key `RMD_*` catalog from the v7.0 mirror of `R3DSDKMetadata.h`.
- Tests: REDline gate detection (set/unset/non-executable), backend selection precedence, parser unit tests (`_parse_redline_keyvalue`, `_canonical_from_redline_fields`, `_canonical_from_exr_attribs` including the `framesPerSecond` rational preference), live e2e tests that skip cleanly when `tests/fixtures/private/red/` is absent.
- README §7 documents both env vars, IPP2/Legacy decode contract, and the partner-vs-consumer dylib distinction.

## v0.2.2

**Fix: ARRI canonical metadata extraction from real ART-CMD exports.**

The v0.2.1 ``_canonical_from_metadata_json`` heuristic walked a flat tree and matched zero keys against ART-CMD's actual export, which nests named sets under ``clipBasedMetadataSets`` / ``descriptiveMetadataSets`` with ``{metadataSetName, metadataSetPayload}`` entries. v0.2.1 callers got ``resolution`` / ``framerate`` / ``pixel_aspect`` all ``None`` from ``read_metadata`` for ARRIRAW.

v0.2.2 verifies against a real ALEXA 35 ARRIRAW HDE ``.arx`` fixture:

- ``resolution``: ``(width, height)`` from ``Image Size.storedSize``.
- ``framerate``: parsed from ``Project Rate.timebase`` (e.g. ``"24/1"`` → ``24.0``).
- ``pixel_aspect``: ``1.0`` (ARRIRAW pixels are square; lens squeeze remains in ``raw_header``).
- ``timecode``: still ``None`` for single-frame ``.ari`` / ``.arx`` — typically sourced from a sidecar; the full ART-CMD export sits under ``raw_header["art_cmd_export"]`` if callers need to dig.

New ``_parse_fraction`` and ``_index_named_sets`` helpers cover ART-CMD's recurring ``"N/D"`` strings and named-set shape. Synthetic unit tests guard the parser without requiring the private fixture; the live metadata test now asserts populated values.

## v0.2.1

**ARRIRAW decode via ART-CMD subprocess backend.**

- `ArriRawReader` now claims **both `.ari` and HDE-compressed `.arx`** extensions (case-insensitive). HDE decompression is provided by ART-CMD's bundled `libcodexhdedecoder`.
- Added second backend: `FORGE_ARRI_ART_PATH` points at the `art-cmd` binary from ARRI Reference Tools. forge-io shells out per call, decoding one frame to ACES AP0 scene-linear EXR (`AP0/D60/linear`, `exr_uncompressed/f16`, `--render-platform cpu`), reads it back via OIIO, and returns `source_colorspace="AP0/D60/linear"` for OCIO transforms downstream.
- `read_metadata` for ARRIRAW uses `art-cmd export --skip-audio --skip-look` for header-only reads (no pixel decode). Canonical metadata extracted best-effort from the JSON; full ART-CMD export retained under `raw_header["art_cmd_export"]`.
- Backend selection: SDK gate (option 1) takes precedence when configured; ART-CMD (option 2) is the fallback; `ArriSdkUnavailableError` names both env vars when neither is set.
- ART-CMD's `--start N` is offset from the clip's first frame, not the absolute frame number — `arri_reader` scans the clip directory once to compute the offset for the requested frame.
- Tests: `.arx` dispatch, ART-CMD env-var gate (set/unset/non-executable), backend selection precedence, sequence offset helpers, live end-to-end e2e tests that skip cleanly when `tests/fixtures/private/arri/` is absent.
- README §7 documents both env vars, the macOS quarantine gotcha (`xattr -dr com.apple.quarantine`), and the EULA stance (subprocess invocation permitted; no redistribution).
- `.gitignore`: `tests/fixtures/private/` for facility-licensed clips.

## v0.2.0

Vendor reader scaffolds + Sony policy + CinemaDNG dispatch fix + OCIO test infrastructure.

**New readers (gate-only, decode pending):**

- RED R3D (`.r3d`, case-insensitive) — `RedRawReader`, `FORGE_RED_SDK_PATH`, `RedSdkUnavailableError`. Dispatched after ARRI, before OIIO. Real decode lives in the **forge-io-red** sibling package per `RED_BINDING_PLAN.md` (see repo root) — pending RED Developer Support replies and SDK install.

**Documented non-features:**

- Sony X-OCN — new `SonyUnsupportedError`. Sony Partner Program SDK access is NDA-gated and not publicly available; no `.mxf` reader is registered (ARRI MXF remains future work). Recommended path: transcode upstream via Sony RAW Viewer's RAW Exporter.

**Format support:**

- CinemaDNG (`.dng`) now dispatches through `OIIOReader` (was previously `UnsupportedFileError`). LibRaw-backed when the OIIO build includes the raw plugin (ASWF `ci-vfxall` ships it). The committed `solid_rgb.dng` smoke fixture is a TIFF-in-DNG hybrid for deterministic CI; a real Bayer-DNG golden remains an open TODO for true LibRaw version pinning.

**Tests / infrastructure:**

- OCIO golden test no longer depends on `CreateFromBuiltinConfig`; uses a committed minimal config (`tests/fixtures/ocio/minimal.ocio`).
- Added `RED_BINDING_PLAN.md` — pybind11 binding strategy for the eventual forge-io-red sibling package, with seven open questions for RED Developer Support.

**Open items (not in this release):**

- Real R3D decode — blocked on RED Developer Support replies + SDK install + `forge-io-red` sibling package scaffolding.
- Real ARRIRAW decode — blocked on ARRI Partner Program processing.
- Bayer-DNG golden fixture for true LibRaw version pin.

## v0.1.1

- Relax `requires-python` to `>=3.11` (remove `<3.14` cap) so Python 3.14+ can install without `--ignore-requires-python`.
- ARRIRAW (`.ari`) reader scaffold: dispatch registered before OIIO; `read` / `read_metadata` raise `ArriSdkUnavailableError` until the ARRI Image SDK decode path is wired.
- SDK discovery via `FORGE_ARRI_SDK_PATH` (coarse `ctypes.CDLL` load gate; symbol / ABI verification deferred to the SDK adapter).
- New public exception: `ArriSdkUnavailableError`.

## v0.1.0

Initial release: OIIO-backed `read` / `read_frame` / `read_metadata` / `resolve_pattern`, optional OCIO `working_space` / `assume_source`, EXR beauty selection rules, and contract tests.
