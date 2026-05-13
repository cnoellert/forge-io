# forge-align ↔ forge-io v0.3.0 R3D integration brief

**For:** Cursor (or any coding agent) working inside `forge-align`.
**Authored from:** `forge-io` v0.3.0 at commit `98579d6`.
**Goal:** validate that `.R3D` decode flows cleanly through forge-align's
existing pipeline, then update align's docs to reflect the new capability.

---

## What changed in forge-io v0.3.0

- New working backend for `.R3D` decode: **REDline subprocess** via
  `FORGE_RED_REDLINE_PATH`. REDline ships inside **REDCINE-X PRO** (free
  download); on macOS the path is typically
  `/Applications/REDCINE-X Professional/REDCINE-X PRO.app/Contents/MacOS/REDline`.
- `forge_io.read('clip.R3D')` returns `float32 (H, W, 3)` RGB,
  `source_colorspace="REDWideGamutRGB/linear"`, `bit_depth=16`, canonical
  metadata populated (resolution, framerate, timecode, pixel_aspect).
- `forge_io.read('clip.R3D', working_space='ACEScg')` applies the OCIO
  transform `REDWideGamutRGB/linear` → `ACEScg`. Same surface as EXR/DPX
  reads — no special-casing needed in callers.
- `read_metadata` is cheap (~0.7s wall, no pixel decode).
- v0.3.0 hard-codes decoding the **first frame** of the clip. Per-frame
  selection inside an `.R3D` is a future kwarg; for now treat each `.R3D`
  as "the clip starts here." Most VFX workflows give you one clip per
  `.R3D` and align reads the first usable frame, so this matches the
  common case — verify it matches align's usage before assuming.

Read `README.md` §7 (RED block) and `CHANGELOG.md` v0.3.0 for the full
contract.

---

## Test plan inside forge-align

### Setup

1. Confirm `forge-align` depends on `forge-io>=0.3.0` (bump
   `pyproject.toml` / `requirements*.txt` if needed). The repo lives at
   `cnoellert/forge-io`, tag `v0.3.0`, commit `98579d6`.
2. In the test environment, export:
   ```
   FORGE_RED_REDLINE_PATH="/Applications/REDCINE-X Professional/REDCINE-X PRO.app/Contents/MacOS/REDline"
   ```
   (or platform equivalent). Without this env var, `.R3D` reads raise
   `RedSdkUnavailableError` with a message naming both env vars.
3. Need a real `.R3D` clip. There's one available at
   `/tmp/A005_W055_0819CM_001.R3D` on this dev Mac (V-RAPTOR XE 8K VV,
   284 frames, IPP2). Add it under `tests/fixtures/private/red/` (or
   align's analog) and gitignore it.

### Validation steps

1. **Sanity read.** `forge_io.read('<clip>.R3D')` returns a sensible
   `Image`. Spot-check pixel range (scene-linear, can well exceed 1.0),
   shape `(H, W, 3)`, dtype `float32`, channels-last RGB.
2. **Metadata-only read.** `forge_io.read_metadata('<clip>.R3D')` populates
   resolution, framerate (exact NTSC rational expected for 23.976 clips),
   timecode (TOD/absolute TC string `HH:MM:SS:FF`), pixel_aspect.
3. **End-to-end align run.** Plug an `.R3D` plate into the existing
   align pipeline (the two read sites in `extractor.py:42` and `:115`,
   per `forge-io-SKETCH.md`). Verify solver convergence on a known-good
   shot. Expected behavior is identical to running the same shot from a
   pre-transcoded EXR sequence, modulo timing.
4. **Container extraction stays.** `extract_container_frame` in
   `extractor.py:63` is **transcode**, not "read pixels" — forge-io has
   nothing to say about that path. Leave it alone.

### Things that should NOT be true after migration

- No `cv2.imread(...)` + manual normalization for `.R3D` paths.
- No `_linear_to_srgb` / `_log_to_srgb` / `_classify_colorspace`
  approximations being applied to RED footage (forge-io's OCIO transform
  replaces them — see migration item 2 in `forge-io-SKETCH.md`).
- No path/filename heuristics inferring colorspace from naming — `forge-io`
  enforces declared-or-`"unknown"`.

---

## Docs in forge-align to update

(Specific files depend on align's repo layout; expected surfaces:)

- `README.md` — formats section. Add `.R3D` to the list of supported
  inputs. Note the `FORGE_RED_REDLINE_PATH` env var requirement and the
  REDCINE-X PRO install pointer. Mirror how align currently documents
  the ARRI ART-CMD requirement (v0.2.1) — if it doesn't yet, that's a
  second gap.
- Any "supported formats" matrix or input-format docs.
- CHANGELOG / release notes — mention forge-io v0.3.0 dependency bump.
- Test docs / fixture docs — describe the new
  `tests/fixtures/private/red/` (gitignored) pattern.
- If align has its own colorspace-handling docs (likely under or near
  `solver.py:282-379`), update to reflect that RED now joins ARRI as a
  vendor-RAW path landing in a known wide-gamut linear intermediate
  (`REDWideGamutRGB/linear` for RED, `AP0/D60/linear` for ARRI).

---

## Acceptance criteria

1. forge-align's test suite passes against forge-io v0.3.0.
2. An `.R3D` plate end-to-end test exists (skipped cleanly when the
   private fixture is absent — matches forge-io's own `tests/fixtures/
   private/red/` pattern).
3. README and supported-formats docs in forge-align name `.R3D` and the
   `FORGE_RED_REDLINE_PATH` env var.
4. No regression in non-RED paths (EXR/DPX/ARRI).

---

## Reference

- forge-io v0.3.0 release: `https://github.com/cnoellert/forge-io/releases/tag/v0.3.0`
- forge-io reader source: `src/forge_io/readers/red_reader.py`
- forge-io README §7: backend matrix, decode contract, partner-vs-consumer
  dylib caveat
- forge-io `RED_BINDING_PLAN.md`: long-term in-process pybind11 binding
  design (not blocking; future optimization, not relevant to this brief)
- forge-io-SKETCH.md "Migration path for forge-align" section: pre-existing
  audit of read sites in `extractor.py` and the
  `_linear_to_srgb` / `_log_to_srgb` / `_classify_colorspace` block in
  `solver.py:282-379`.
