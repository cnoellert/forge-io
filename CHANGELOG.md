# Changelog

## Unreleased

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
