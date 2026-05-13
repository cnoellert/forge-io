# Changelog

## Unreleased

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
