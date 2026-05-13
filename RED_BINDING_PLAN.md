# RED R3D SDK — Python Binding Strategy Plan

**Status:** Plan, not implementation. Author this against the public header at
`https://github.com/arunabhcode/RedSDK/blob/master/include/red/R3DSDK.h`
(v7.0 mirror; API shape reportedly unchanged through 9.x). Validate every
assumption here against the actual SDK download once a developer can install
9.2.0.

The companion header `R3DSDKDefinitions.h` (in the same `include/red/`
directory) carries the enums and the `VideoDecodeJob` struct definition; both
headers must be consulted together.

---

## 1. C-API check — verdict: **no C API exists**

A full text scan of both `R3DSDK.h` and `R3DSDKDefinitions.h` returns **zero**
`extern "C"` declarations. Every public entry point is inside
`namespace R3DSDK { ... }` (R3DSDK.h:23, :511). The free functions
(`InitializeSdk`, `FinalizeSdk`, `GetSdkVersion`, `LoadCreativeLut`,
`UnloadCreativeLut`, `SaveRWGLog3G10ToOutputTransform3DLut`,
`CreateRWGLog3G10ToOutputTransformLuts`) are C++‑linkage namespaced symbols,
not C symbols. The primary surface is the class `Clip` (R3DSDK.h:51–358) plus
the POD struct `VideoDecodeJob` (R3DSDKDefinitions.h:599–651).

**Consequence:** `ctypes.CDLL` against the shipped dylib is not viable for
calling the class methods we care about. Name mangling is compiler-dependent
(`itanium-abi` on Clang/GCC vs. MSVC mangling on Windows) and the SDK binaries
are built with the vendor's toolchain, not ours. We need a native shim.
Options 2 (extern "C" shim) and 3 (pybind11) are the only viable paths.

## 2. v0.1 surface we must wrap

All quoted signatures are from `R3DSDK.h` unless otherwise noted.

**SDK lifecycle (free functions, R3DSDK.h:36–46):**
```cpp
InitializeStatus InitializeSdk(const char * pathToDynamicLibraries, unsigned int optional_components);
void FinalizeSdk();
const char * GetSdkVersion();
```
Flag we pass: `OPTION_RED_NONE = 0x00` (R3DSDK.h:26) for CPU‑only.

**Clip construction / lifetime (R3DSDK.h:59–85):**
```cpp
Clip();
Clip(const char * pathToFile);          // UTF-8
~Clip();
LoadStatus Status() const;
LoadStatus LoadFrom(const char * pathToFile);
void Close();
```
Copy/assign are private (R3DSDK.h:354–355) — clips are non‑copyable; the shim
must hand back opaque handles, not values.

**Clip introspection (R3DSDK.h:250–297):**
```cpp
bool         Uuid(unsigned char * uuid) const;   // 16 bytes
size_t       Width() const;
size_t       Height() const;
size_t       VideoFrameCount() const;
float        VideoAudioFramerate() const;
float        TimecodeFramerate() const;
const char * Timecode(size_t videoFrameNo);      // NOT const — buffer is per-clip scratch
```
The `Timecode()` doc warns the returned pointer is invalidated by the next
call (R3DSDK.h:292–297). The shim must `strdup` before returning to Python or
the Python wrapper must copy immediately.

**Color & ISO metadata** come through the metadata DB, not direct accessors
(R3DSDK.h:332–346):
```cpp
size_t       MetadataCount() const;
bool         MetadataExists(const char * key) const;
std::string  MetadataItemKey(size_t index) const;
unsigned int MetadataItemAsInt(const char * key) const;
std::string  MetadataItemAsString(const char * key) const;
float        MetadataItemAsFloat(const char * key) const;
```
**Critical:** these return `std::string` by value. `std::string` has no stable
ABI — this alone disqualifies any FFI strategy that does not own the C++
compilation unit. The shim must convert to `const char *` (and free correctly)
or pybind11 must handle it. Key names (ISO, white balance Kelvin, color
space, gamma) live in `R3DSDKMetadata.h` — fetch that header on day 1 and
enumerate the `RMD_*` constants.

**Decode (R3DSDK.h:133, struct at R3DSDKDefinitions.h:599–651):**
```cpp
DecodeStatus DecodeVideoFrame(size_t videoFrameNo, const VideoDecodeJob & decodeJob) const;

struct VideoDecodeJob {
    VideoDecodeMode             Mode;            // e.g. DECODE_FULL_RES_PREMIUM = 'DFRP'
    VideoPixelType              PixelType;       // e.g. PixelType_HalfFloat_RGB_Interleaved
    void *                      OutputBuffer;    // CALLER allocates, 16-byte aligned
    size_t                      BytesPerRow;     // multiple of 16
    size_t                      OutputBufferSize;
    ImageProcessingSettings *   ImageProcessing; // optional, NULL = clip defaults
    HdrProcessingSettings *     HdrProcessing;   // optional, NULL = main track
    Metadata *                  OutputFrameMetadata;
    VideoDecodeJob();
};
```
**Caller allocates the output buffer** (R3DSDKDefinitions.h:616–629). For
forge-io this means we allocate a NumPy array on the Python side and pass
`arr.ctypes.data` (or pybind11 buffer protocol) into the job. Buffer must be
16‑byte aligned — verify NumPy's default allocator satisfies this on all
target platforms, or use `numpy.empty_aligned`/posix_memalign in the shim.

For v0.1 we should restrict to:
- `Mode = DECODE_FULL_RES_PREMIUM` (R3DSDKDefinitions.h:474)
- `PixelType = PixelType_HalfFloat_RGB_Interleaved` (R3DSDKDefinitions.h:513) —
  this is the only float output, drops the matrix/curve fields per the header
  comment, and lands us in scene-linear automatically (see §5).

## 3. Threading & memory model

- **`InitializeSdk` is global per process.** Header banner (R3DSDK.h:31–33):
  *"This must be called one time before calling any other functions ... Do not
  call this for each single thread."* forge-io must reference-count
  init/finalize at module load, mirroring the OIIO reader pattern. Multiple
  init calls are not promised safe; treat as undefined.
- **`Clip` is thread-safe except during `LoadFrom`/`Close`** (R3DSDK.h:49–50).
  Decode jobs on the same Clip from multiple threads are allowed. This means
  forge-io's reader can be reused across threads after construction without a
  global lock — but we must serialize `LoadFrom`/`Close` per Clip.
- **Caller owns the output buffer** for `DecodeVideoFrame`. The SDK never
  allocates pixel memory on our behalf.
- **`DecodeAudioBlock` is explicitly NOT thread-safe** (R3DSDK.h:149). Not in
  v0.1 scope but worth noting.
- The library banner says *"The SDK is thread-safe for the most part, but it
  may synchronize access when needed"* (R3DSDK.h:8–9) — assume internal
  serialization; don't expect linear scaling across decode threads.

## 4. Recommended binding strategy — **pybind11**

Since there is no C API, we choose between (2) an `extern "C"` C++ shim called
through `ctypes`, and (3) pybind11.

**Recommendation: pybind11.** Rationale:

- The surface uses `std::string` return-by-value and `size_t` everywhere. A
  ctypes shim has to manually convert every string return; pybind11 does this
  automatically and correctly.
- We need to pass NumPy buffer pointers into the decode job. pybind11 +
  `py::buffer_protocol` handles alignment, lifetime, and shape declaration in
  ~5 lines; ctypes requires custom `ctypes.Structure` mirrors of
  `VideoDecodeJob` that we must keep in sync by hand.
- Exception → Python translation is automatic with pybind11; with ctypes we'd
  hand-marshal every `DecodeStatus` enum.
- The shim is small enough (~250–350 LoC) that either approach builds fast,
  but pybind11 produces dramatically less Python‑side glue.

**Estimated shim size:** ~300 LoC C++ wrapping:
- `init_sdk(path: str, flags: int) -> int`
- `finalize_sdk() -> None`
- `sdk_version() -> str`
- `class Clip` with `load(path)`, `close()`, `status()`, `width()`,
  `height()`, `frame_count()`, `framerate()`, `timecode(frame)`,
  `metadata_keys()`, `metadata_int/str/float(key)`,
  `decode_frame_half_float(frame_no, numpy_array)`.

**Location:** `src/forge_io/readers/red/_r3d_ext.cpp` + companion
`pyproject.toml` build hook. Mirror the ARRI scaffold pattern in
`src/forge_io/readers/arri_reader.py`; the Python entrypoint stays in
`red_reader.py` and only imports the extension when
`FORGE_RED_SDK_PATH` resolves at startup.

**User install experience:**
- **Sdist + source build:** forge-io ships the `_r3d_ext.cpp` source.
  pyproject.toml uses `scikit-build-core` or `setuptools` + pybind11. User
  must have a C++17 compiler. Build skips silently if
  `FORGE_RED_SDK_PATH` isn't set at build time — fall back to a stub like
  the ARRI scaffold. Trade‑off: every user compiles.
- **Pre-built wheels (preferred long-term):** ship per-platform,
  per-Python-version wheels (cp39/310/311/312/313 × macos-arm64/macos-x86_64/
  linux-x86_64-manylinux_2_28/win-amd64). The wheel contains the compiled
  extension linked against the SDK's import library/SO **stub** — at
  runtime, `FORGE_RED_SDK_PATH` points to the actual dylib which the SDK
  itself loads via `InitializeSdk`. This keeps the SDK out of the wheel
  (license compliant) while shipping no source.
- pybind11 wheels are Python-version-specific. ABI3 (`Py_LIMITED_API`) is
  possible with pybind11 ≥ 2.12 and reduces the matrix to one wheel per
  platform — recommend ABI3 from day 1.

**Why not the C++ shim + ctypes route?** Strictly speaking it works, but it
duplicates work pybind11 already automates (string marshalling, buffer
protocol, enum translation, exception conversion) and produces no portability
benefit. Build complexity is identical: both require compiling C++ at install
or wheel time.

## 5. Color science

The decoded color space depends entirely on `VideoDecodeJob.PixelType` and
`ImageProcessingSettings`. Relevant defaults from `R3DSDKDefinitions.h`:

- **`PixelType_HalfFloat_RGB_Interleaved`** (line 513) ignores `GammaCurve`
  and forces linear output. Matrix is still controlled by
  `ImageProcessingSettings.ColorSpace`.
- **`PixelType_HalfFloat_RGB_ACES_Int`** (line 525) ignores both `GammaCurve`
  and `ColorSpace` — always lands in ACES AP0 linear. Useful but locks us out
  of REDWideGamutRGB; defer to v0.2.
- Available color spaces include `ImageColorREDWideGamutRGB` (line 152),
  `ImageColorRec709` (line 155), `ImageColorRec2020`, `ImageColorDCIP3D65`,
  etc.
- Available gamma curves include `ImageGammaLinear` (line 120),
  `ImageGammaLog3G10` (line 126), `ImageGammaBT1886`, `ImageGamma2_2`, etc.
- Default `ColorVersion` is `ColorVersion3` (IPP2) for in-camera IPP2 clips,
  `ColorVersion2` (Legacy) otherwise (R3DSDK.h:226–228).

**Recommended forge-io defaults:**

- Decode `PixelType_HalfFloat_RGB_Interleaved`, set `ImageProcessing` to NULL
  (use clip defaults), which on an IPP2 clip yields **REDWideGamutRGB
  primaries with linear transfer** (since half-float forces linear).
- Report `source_colorspace = "red_widegamutrgb_linear"` when we can confirm
  the clip is IPP2 (check `Clip::DefaultColorVersion()`); otherwise
  `source_colorspace = "unknown"` per the existing forge-io declared-or-
  unknown policy (`arri_reader.py:22–24`, `oiio_reader.py:_declared_colorspace_from_spec`).
- Expose `ImageProcessingSettings.ColorSpace` and `.GammaCurve` overrides in a
  v0.2 keyword arg (`red_target_colorspace=...`). v0.1 stays
  "decode at clip default, report what we got."

## 6. Metadata keys (R3DSDKMetadata.h v7.0 mirror)

`R3DSDKMetadata.h` was fetched from the same public v7.0 mirror that backs §1
of this plan. It declares **133** `RMD_*` keys as `static const char[]` string
constants inside `namespace R3DSDK`. The shim does not need to expose
`RMD_*` symbols — every accessor takes the **string value** (e.g. `"iso"`),
so the Python side can just pass literals. Names below are the v7.0 strings;
diff against 9.2.0's header once the SDK download is in hand (see §7.1).

**Nine keys are documented "Guaranteed to exist":**

| Constant | String | Type | Use |
|---|---|---|---|
| `RMD_IMAGE_WIDTH` | `image_width` | Int | resolution.x fallback |
| `RMD_IMAGE_HEIGHT` | `image_height` | Int | resolution.y fallback |
| `RMD_FRAMERATE_NUMERATOR` | `framerate_numerator` | Int | framerate fallback (num) |
| `RMD_FRAMERATE_DENOMINATOR` | `framerate_denominator` | Int | framerate fallback (den) |
| `RMD_ISO` | `iso` | Float | raw_header |
| `RMD_EXPOSURE_COMPENSATION` | `exposure_compensation` | Float | raw_header (Legacy color version only — see header line 85) |
| `RMD_START_ABSOLUTE_TIMECODE` | `start_absolute_timecode` | String `"01:00:00:00"` | timecode (when pref=1) |
| `RMD_START_EDGE_TIMECODE` | `start_edge_timecode` | String `"01:00:00:00"` | timecode (when pref=0) |
| `RMD_USER_TIMECODE_PREFERENCE` | `user_timecode_preference` | Int (0=edge/run-record, 1=absolute) | selects between the two `START_*` keys |

For every non-guaranteed key, call `Clip::MetadataExists(key)` before the typed
accessor — `MetadataItemAsInt/Float/String` are undefined when the key is
absent.

### 6.1 Canonical `ImageMetadata` field mapping

forge-io's canonical keys are `resolution`, `pixel_aspect`, `timecode`,
`framerate` (`src/forge_io/_types.py:15`). Prefer the **direct `Clip::`
accessor** over the metadata DB where one exists — fewer string lookups,
fewer "key absent" branches, no `std::string` round-trip.

| forge-io canonical | Primary source | Fallback (metadata DB) |
|---|---|---|
| `resolution` | `Clip::Width()` / `Clip::Height()` (R3DSDK.h:250–254) — `size_t` | `RMD_IMAGE_WIDTH`, `RMD_IMAGE_HEIGHT` |
| `framerate` | `Clip::VideoAudioFramerate()` (R3DSDK.h:269) — `float` | `RMD_FRAMERATE_NUMERATOR / RMD_FRAMERATE_DENOMINATOR` |
| `timecode` (start) | `Clip::Timecode(0)` (R3DSDK.h:292; remember **scratch buffer** — `strdup` in the shim before returning) | branch on `RMD_USER_TIMECODE_PREFERENCE`; read `RMD_START_ABSOLUTE_TIMECODE` (pref=1) or `RMD_START_EDGE_TIMECODE` (pref=0) |
| `pixel_aspect` | `RMD_PIXEL_ASPECT_RATIO` (Float) | derive from `RMD_CLIP_ASPECT_NUMERATOR / RMD_CLIP_ASPECT_DENOMINATOR`; cross-check `RMD_ANAMORPHIC_MODE` (0=none, 1=2×, 2=1.3×, 3=1.25×) — if anamorphic, the lens squeeze is *separate* from the sensor's PAR and should land in `raw_header`, not `pixel_aspect`. ARRIRAW does the same (v0.2.2 CHANGELOG). |

### 6.2 Color-science metadata (drives `source_colorspace` reporting)

| Key | Type | Why we read it |
|---|---|---|
| `RMD_CLIP_DEFAULT_COLOR_VERSION` | Int | `2` = Legacy (ColorVersion2), `3` = IPP2 (ColorVersion3). Same field as `Clip::DefaultColorVersion()` (R3DSDK.h:226–228). Drives §5 decision: when `3`, decode with NULL `ImageProcessingSettings` lands in REDWideGamutRGB/linear (because half-float pixel type ignores gamma curve). When `2`, the default decode is Legacy primaries and we report `source_colorspace = "unknown"` per the declared-or-unknown policy. |
| `RMD_CREATIVE_LUT` | String | IPP2-only sidecar 3D LUT filename. Surface in `raw_header` so downstream tools can find it; forge-io does **not** apply it (display-transform = out of scope). |
| `RMD_HDR_MODE` | (see header) | Indicates HDRx track presence; relevant when `VideoDecodeJob.HdrProcessing` is non-NULL. Out of v0.1 scope but record. |

### 6.3 `raw_header` payload for v0.1

Surface this superset (read with `MetadataExists` gate, never blind-typed):

- **Camera/sensor:** `RMD_CAMERA_MODEL`, `RMD_CAMERA_MODEL_ID`, `RMD_CAMERA_PIN`, `RMD_CAMERA_FIRMWARE_VERSION`, `RMD_SENSOR_NAME`, `RMD_SENSOR_ID`, `RMD_SENSOR_OLPF_NAME`, `RMD_SENSOR_SENSITIVITY_NAME`
- **Clip identity:** `RMD_CLIP_ID`, `RMD_REEL_ID`, `RMD_ORIGINAL_FILENAME`
- **Exposure / WB:** `RMD_ISO`, `RMD_ISO_CALIBRATION_VERSION`, `RMD_EXPOSURE_TIME` (μs), `RMD_WHITE_BALANCE_KELVIN`, `RMD_WHITE_BALANCE_TINT`, `RMD_SHUTTER_DEGREES`, `RMD_SHUTTER_FRACTIONS`
- **Color/IPP2:** `RMD_CLIP_DEFAULT_COLOR_VERSION`, `RMD_CREATIVE_LUT`, `RMD_DRX`, `RMD_BRIGHTNESS`, `RMD_CONTRAST`, `RMD_SATURATION`, `RMD_DIGITAL_GAIN_RED/GREEN/BLUE`, `RMD_FLUT_CONTROL`
- **Orientation:** `RMD_FLIP_HORIZONTAL`, `RMD_FLIP_VERTICAL`, `RMD_ANAMORPHIC_MODE`
- **Timing:** `RMD_FRAMERATE_NUMERATOR`, `RMD_FRAMERATE_DENOMINATOR`, `RMD_RECORD_FRAMERATE`, `RMD_USER_TIMECODE_PREFERENCE`, `RMD_START_ABSOLUTE_TIMECODE`, `RMD_START_EDGE_TIMECODE`

The remaining ~100 keys (IMU readings, frame-guide overlays, user-curve
control points, audio routing, etc.) are reachable through
`Clip::MetadataItemAsString(key)` for callers who need them — we just don't
enumerate them in `raw_header` by default. `MetadataCount()` + iteration via
`MetadataItemKey(i)` (R3DSDK.h:332–337) gives the full dump if a debugging
path needs it.

### 6.4 Gotchas captured from the header

- `RMD_DROPPED_FRAME_COUNT` exists — surface in `raw_header` so traffik or
  QC tools can flag dropped-frame clips.
- `RMD_EXPOSURE_COMPENSATION` is **Legacy-only**; only "guaranteed" inside
  ColorVersion2 clips. For IPP2 clips read `RMD_EXPOSURE_ADJUST` instead.
- `MetadataType` enum (header lines ~17–24) has four values — `Invalid`,
  `Int`, `String`, `Float`. The accessors auto-convert with rules tabulated
  in the header comment block; calling `MetadataItemAsFloat` on a String
  value parses the string. Prefer the matched accessor for the documented
  type to avoid hidden parse errors.

---

## 7. Open questions for RED Developer Support

Public header does not answer:

1. **ABI guarantees across 7.x → 9.x.** The header banner explicitly says
   "Do *NOT* use this header file with any other version of the R3D SDK
   library!" (R3DSDK.h:1–2). We need the 9.2.0 header from the actual
   download; confirm `VideoDecodeJob` layout is unchanged before relying on
   the public mirror.
2. **`std::string` ABI.** Which C++ standard library and ABI flag
   (`_GLIBCXX_USE_CXX11_ABI`) are the Linux binaries built against? The
   `MetadataItemAsString`/`MetadataItemKey` returns will crash if our shim's
   libstdc++ ABI disagrees with the SDK's. Likely answer: GCC 7+ new ABI,
   but must be confirmed.
3. **Wheel-time linkage.** Is there a stub `.lib`/`.tbd`/`.so` we can link
   against at wheel build time, separate from the runtime `libR3DSDK.dylib`
   the user installs? If not, wheels must dlopen the runtime path lazily,
   which means our extension exports its own symbols and resolves SDK
   symbols at `InitializeSdk` time — possible but more elaborate.
4. **Output buffer alignment in practice.** Header says 16‑byte; modern
   NumPy default allocators give 16 on most platforms but it's not
   guaranteed by the Python data model. Confirm whether 32-byte (AVX) is
   preferred for performance even if 16 is the minimum.
5. **Metadata key parity 7.0 → 9.2.0.** The v7.0 `R3DSDKMetadata.h` is now
   enumerated in §6 (133 keys; 9 documented guaranteed). Confirm the
   string values are unchanged in 9.2.0 — especially `RMD_CLIP_DEFAULT_COLOR_VERSION`
   (drives our `source_colorspace` branch) and the timecode pair
   (`RMD_START_*_TIMECODE` + `RMD_USER_TIMECODE_PREFERENCE`). New keys in
   9.2.0 may exist (HDR, gyro, lens-mount) but are additive — not blocking
   v0.1.
6. **Re-entrancy of `InitializeSdk`.** Can we call it a second time after
   `FinalizeSdk` in the same process (e.g. test teardown / reload)? Header
   says "must be called one time" but doesn't say "exactly once forever."
7. **License text** for the actual 9.2.0 license file — confirm the
   "private non-shared directory" wording and that runtime-only redistribution
   via env-var pointer is permitted. We are mirroring the ARRI pattern but
   the license text must back it.

---

**Next concrete step for the implementer:** §6 now catalogues the v7.0
`RMD_*` keys and the canonical-field mapping, so the remaining gating work
is **(a)** install the 9.2.0 SDK and diff `include/R3DSDK.h` +
`R3DSDKMetadata.h` against this plan's references (§7.1, §7.5), and
**(b)** confirm the linkage / ABI questions (§7.2, §7.3, §7.7) with RED
Developer Support. Once those clear, the shim outline in §4 plus the key
catalog in §6 are enough to start writing `_r3d_ext.cpp` in the
`forge-io-red` sibling.
