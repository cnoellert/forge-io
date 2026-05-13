# Changelog

## Unreleased

- RED R3D (`.r3d`) reader scaffold: dispatch after ARRI, before OIIO; `read` / `read_metadata` raise `RedSdkUnavailableError` until the R3D SDK gate is satisfied; decode lives in the **forge-io-red** sibling package per `RED_BINDING_PLAN.md`.
- `FORGE_RED_SDK_PATH` coarse `ctypes.CDLL` discovery (symbol verification deferred to forge-io-red).
- New public exception: `RedSdkUnavailableError`.

## v0.1.1

- Relax `requires-python` to `>=3.11` (remove `<3.14` cap) so Python 3.14+ can install without `--ignore-requires-python`.
- ARRIRAW (`.ari`) reader scaffold: dispatch registered before OIIO; `read` / `read_metadata` raise `ArriSdkUnavailableError` until the ARRI Image SDK decode path is wired.
- SDK discovery via `FORGE_ARRI_SDK_PATH` (coarse `ctypes.CDLL` load gate; symbol / ABI verification deferred to the SDK adapter).
- New public exception: `ArriSdkUnavailableError`.

## v0.1.0

Initial release: OIIO-backed `read` / `read_frame` / `read_metadata` / `resolve_pattern`, optional OCIO `working_space` / `assume_source`, EXR beauty selection rules, and contract tests.
