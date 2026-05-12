# Changelog

## Unreleased

- ARRIRAW (`.ari`) reader scaffold: dispatch registered before OIIO; `read` / `read_metadata` raise `ArriSdkUnavailableError` until the ARRI Image SDK is wired.
- SDK discovery via `FORGE_ARRI_SDK_PATH` (coarse `ctypes.CDLL` load gate; symbol / ABI verification deferred to the SDK adapter).
- New exception types: `ArriSdkUnavailableError`, `ImageDecodeError`.

## v0.1.0

Initial release: OIIO-backed `read` / `read_frame` / `read_metadata` / `resolve_pattern`, optional OCIO `working_space` / `assume_source`, EXR beauty selection rules, and contract tests.
