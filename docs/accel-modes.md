# Acceleration modes of the two drivers

Groundwork for letting QuadSens edit acceleration settings. This document
describes, from the sources only, how the yeetmouse fork (Linux) and RawAccel
(Windows) implement their acceleration settings, and how the two map onto each
other.

**Status after verification.** An independent check compared this document
with both drivers' sources: it re-implemented each driver's formulas from its
source alone, compared them numerically, and ran the fork's unmodified
fixed-point C code (`accel_modes.c` with `FixedMath`) in a userspace build.
No kernel module was loaded and no real RawAccel was run. Statements that this
check confirmed numerically are marked "(verified numerically)"; its
corrections are worked in and marked "(corrected)". Sections 7 and 8 are new
and hold the rules QuadSens' editor enforces and what the check found in the
fork. Where section 4 and section 7 differ, section 7 is what QuadSens does.

**Nothing in this document was tested on hardware or against a running driver.**
Everything was read from source code. Every factual statement is marked either
`(source: file:line)` or `(inferred)`. "Inferred" means: concluded from the
cited code, from algebra, or from general knowledge of the platform, and not
stated by the code itself. A few algebraic equivalences were additionally
checked numerically in double precision with a small script that re-implements
the formulas; that checks the algebra, not the drivers.

Sources:

- "the yeetmouse fork": the fork with directional scaling, version
  `0.9.5+dirscale1` (source: version.mk:1). Paths are relative to its
  repository root.
- RawAccel at the tags `v1.7.1` and `v1.6.1`. Paths are relative to its
  repository root, with the tag in brackets, for example
  `common/rawaccel.hpp:320 (v1.7.1)`.
- QuadSens itself, paths relative to this repository.

Notation: `v` is the speed that goes into a curve, `f(v)` the multiplier the
curve returns ("sensitivity"), `ln` the natural logarithm, `^` a power.

---

## 1. The yeetmouse fork

### 1.1 Modes

The mode numbers are an enum shared by driver and GUI (source:
shared_definitions.h:6-18).

| Number | Enum name | GUI name | Curve function in the driver |
|---|---|---|---|
| 0 | `AccelMode_Current` | Current | none, multiplier 1 (source: driver/accel.c:286-288) |
| 1 | `AccelMode_Linear` | Linear | `accel_linear` (source: driver/accel.c:262-264) |
| 2 | `AccelMode_Power` | Power | `accel_power` (source: driver/accel.c:265-267) |
| 3 | `AccelMode_Classic` | Classic | `accel_classic` (source: driver/accel.c:268-270) |
| 4 | `AccelMode_Motivity` | Motivity | `accel_motivity` (source: driver/accel.c:271-273) |
| 5 | `AccelMode_Synchronous` | Synchronous | `accel_synchronous` (source: driver/accel.c:274-276) |
| 6 | `AccelMode_Natural` | Natural | `accel_natural` (source: driver/accel.c:277-279) |
| 7 | `AccelMode_Jump` | Jump | `accel_jump` (source: driver/accel.c:280-282) |
| 8 | `AccelMode_Lut` | LUT | `accel_lut` (source: driver/accel.c:283-285) |
| 9 | `AccelMode_CustomCurve` | Custom Curve | `accel_lut`, same as 8 (source: driver/accel.c:283-285) |

- GUI names (source: gui/DriverHelper.h:52-79); enum strings used in the config
  file (source: gui/DriverHelper.h:81-108).
- Mode 0 is described as "denotes lack of a curve on the driver side" (source:
  shared_definitions.h:7). It is the only "no acceleration" setting; there is
  no separate "off" mode (inferred).
- The compiled-in default mode is 1, Linear, with `Acceleration` 0.1 (source:
  driver/defaults.h:2, driver/defaults.h:13). A freshly loaded module therefore
  accelerates (inferred).
- Mode 9 is a Bezier curve that exists only in the GUI; the GUI turns it into
  LUT points and the driver treats it as a LUT (source: driver/accel.c:78,
  driver/accel.c:283-285).

### 1.2 Module parameters

All parameters live in `/sys/module/yeetmouse/parameters/` (source:
gui/DriverHelper.h:12) and have the mode `0660` (source: driver/accel.c:27,
driver/accel.c:32, driver/accel.c:38, driver/accel.c:43, driver/accel.c:49).

There are three kinds:

- "float" parameters are kernel `charp` strings. The driver keeps the string
  and a separate fixed-point value; the string is converted only when an update
  is triggered (source: driver/accel.c:24-28, driver/accel.c:98,
  driver/accel.c:117-133).
- `update` and `UseSmoothing` are kernel `byte` parameters bound directly to the
  variable the driver uses (source: driver/accel.c:30-33, driver/accel.c:55,
  driver/accel.c:72). A write takes effect at once (inferred).
- `LutDataBuf` and `_CustomCurveDataAggregate` are fixed string buffers of 4096
  bytes (source: driver/accel.c:41-44, driver/accel_modes.h:15).

| Parameter | Kernel type | Default | Meaning | Source |
|---|---|---|---|---|
| `update` | byte | 1 | Write `1` to make the driver re-read all other parameters | driver/accel.c:55 |
| `AccelerationMode` | charp, unsigned integer as text | 1 | Mode number from 1.1 | driver/accel.c:58, driver/accel.c:135, driver/defaults.h:2 |
| `Sensitivity` | charp, decimal | 1 | Final multiplier; X only when `RatioYX` is not 1 | driver/accel.c:62, driver/defaults.h:5 |
| `RatioYX` | charp, decimal | 1 | Y multiplier relative to X | driver/accel.c:63, driver/defaults.h:6 |
| `OutputCap` | charp, decimal | 0 | Upper limit of the final multiplier, 0 = off | driver/accel.c:64, driver/accel.c:301, driver/defaults.h:7 |
| `InputCap` | charp, decimal | 0 | Upper limit of the speed fed to the curve, 0 = off | driver/accel.c:61, driver/accel.c:242, driver/defaults.h:8 |
| `Offset` | charp, decimal | 0 | Subtracted from the speed before the curve | driver/accel.c:65, driver/accel.c:249, driver/defaults.h:9 |
| `PreScale` | charp, decimal | 1 | Multiplies the raw deltas ("adjust for the DPI") | driver/accel.c:66, driver/accel.c:232-235, driver/defaults.h:10 |
| `Acceleration` | charp, decimal | 0.1 | Mode specific, see 1.5 | driver/accel.c:68, driver/defaults.h:13 |
| `Exponent` | charp, decimal | 1.8 | Mode specific, see 1.5 | driver/accel.c:69, driver/defaults.h:16 |
| `Midpoint` | charp, decimal | 1.3 | Mode specific, see 1.5 | driver/accel.c:70, driver/defaults.h:14 |
| `Motivity` | charp, decimal | 1.5 | Mode specific, see 1.5 | driver/accel.c:71, driver/defaults.h:15 |
| `UseSmoothing` | byte | 1 | Mode specific switch, see 1.5 | driver/accel.c:72, driver/defaults.h:17 |
| `LutSize` | charp, unsigned integer as text | 0 | Number of LUT points, at most 128 | driver/accel.c:75, driver/accel.c:134-137, driver/accel_modes.h:14 |
| `LutDataBuf` | string, 4096 bytes | `0` | LUT points as text `x,y;x,y;...` | driver/accel.c:76, driver/accel.c:140-147, driver/defaults.h:34 |
| `_CustomCurveDataAggregate` | string, 4096 bytes | empty | GUI data of the custom curve, not used by the driver | driver/accel.c:78 |
| `RotationAngle` | charp, decimal, radians | 0 | Rotation of the movement | driver/accel.c:80, driver/defaults.h:24 |
| `AngleSnap_Threshold` | charp, decimal, radians | 0 | Width of the snapping zone, 0 = off; reset to 0 outside [0, pi) | driver/accel.c:81, driver/accel.c:167-169, driver/defaults.h:20 |
| `AngleSnap_Angle` | charp, decimal, radians | 0 | Direction snapped to | driver/accel.c:82, driver/defaults.h:21 |
| `DirScaleXPos` | charp, decimal | 1 | Multiplier for raw input moving right (x > 0) | driver/accel.c:84, driver/defaults.h:27 |
| `DirScaleXNeg` | charp, decimal | 1 | Multiplier for raw input moving left | driver/accel.c:85, driver/defaults.h:28 |
| `DirScaleYPos` | charp, decimal | 1 | Multiplier for raw input moving down (y > 0) | driver/accel.c:86, driver/defaults.h:29 |
| `DirScaleYNeg` | charp, decimal | 1 | Multiplier for raw input moving up | driver/accel.c:87, driver/defaults.h:30 |

### 1.3 Number format, fixed point, and how a write takes effect

- The driver computes in signed 64 bit fixed point with 32 fractional bits
  (source: driver/FixedMath/Fixed64.h:57, driver/FixedMath/Fixed64.h:66).
- A decimal parameter is parsed by `FP64_FromString`: optional sign, digits,
  optional `.` and digits. Anything else (for example an exponent such as
  `5e-05`) makes the parse fail, and then the old value stays in use (source:
  driver/FixedMath/Fixed64.h:1526, driver/accel.c:98). The directional scaling
  notes say the same: "no exponent notation" (source:
  DIRECTIONAL_SCALING.md:21-22).
- The GUI and `yeetmousectl` write floats with the C++ stream default format
  (source: gui/DriverHelper.cpp:74). That format switches to exponent notation
  for small values, so a value such as 0.00005 written by them would not be
  parsed (inferred). QuadSens already writes fixed notation with six decimals
  (source: quadsens/driver.py:51-53).
- Nothing is applied when a parameter file is written. The driver re-reads all
  strings only when `update` is 1, and it checks that only while handling a
  mouse event, and at most once per second (source: driver/accel.c:107-113,
  driver/accel.c:221).
- After the re-read the driver sets `update` back to 0 (source:
  driver/accel.c:112). Reading `update` as 0 therefore means "the last trigger
  was consumed" (inferred).
- `update` starts as 1, so the defaults are parsed at the first mouse event
  after the module is loaded (source: driver/accel.c:55, inferred).
- `UseSmoothing` changes immediately when written, but the constants derived
  from it are only recomputed on the next update (source: driver/accel.c:72,
  driver/accel.c:171, driver/accel_modes.c:13). Between the write and the
  update the curve can be evaluated with constants that do not fit (inferred).
- Reading a `charp` parameter returns the last string written, not the value
  the driver uses (inferred from driver/accel.c:24-28).
- When the driver rejects a parameter combination it sets its internal mode to
  0 and logs a line with `printk`; the string in `AccelerationMode` is not
  changed (source: driver/accel_modes.c:25-27, driver/accel_modes.c:52-54).
  So a rejected setting cannot be detected by reading sysfs back (inferred).

### 1.4 Processing order

One call of `accelerate()` handles the summed X and Y of one input report
(source: driver/driver.c:41-72). Steps in order:

1. Time since the previous call in milliseconds, from `ktime_get()`; values
   above 100 are set to 100 (source: driver/accel.c:198-215). There is no lower
   limit (source: driver/accel.c:211-215). The time stamp is one static
   variable of the function, not one per device (source: driver/accel.c:185),
   so with two mice the time is measured between reports of either (inferred).
2. Parameter update if triggered (source: driver/accel.c:221).
3. **Directional scaling**: `dx` is multiplied by `DirScaleXPos` if `dx > 0`,
   otherwise by `DirScaleXNeg`; `dy` by `DirScaleYPos` if `dy > 0`, otherwise
   by `DirScaleYNeg` (source: driver/accel.c:224-229).
4. Pre-scale: `dx` and `dy` are multiplied by `PreScale` (source:
   driver/accel.c:232-235). The deltas keep this factor to the end, so
   `PreScale` scales both the speed and the output (inferred).
5. Speed: `v = sqrt(dx^2 + dy^2) / ms`, in counts per millisecond of the
   already scaled deltas (source: driver/accel.c:238-239). The time is the
   measured one; no polling rate is assumed (inferred).
6. Input cap: if `InputCap > 0` and `v > InputCap` then `v = InputCap` (source:
   driver/accel.c:242-247). Only the curve argument is limited, the deltas are
   not (inferred).
7. Offset: `v = v - Offset` (source: driver/accel.c:249).
8. Rotation of `(dx, dy)` by `RotationAngle`: `x' = x cos a - y sin a`,
   `y' = x sin a + y cos a` (source: driver/accel.c:252-256).
9. Curve: if `v > 0` the multiplier is `f(v)` of the selected mode; otherwise
   it is the constant `f(0.001)` computed at update time (source:
   driver/accel.c:260-292, driver/accel_modes.c:234-263,
   driver/accel_modes.h:70).
10. Sensitivity. With `RatioYX == 1`: `m = f * Sensitivity`, then
    `m = min(m, OutputCap)` if `OutputCap > 0`, then `dx *= m`, `dy *= m`.
    Otherwise `mx = f * Sensitivity`, `my = mx * RatioYX`, each limited by
    `OutputCap`, then `dx *= mx`, `dy *= my` (source: driver/accel.c:296-320).
11. Angle snapping, if `AngleSnap_Threshold` is not 0: when the direction of
    the output is within half the threshold of `AngleSnap_Angle` (or of its
    opposite), the output is put exactly on that line with its length kept
    (source: driver/accel.c:323-338, driver/accel_modes.c:269-271).
12. The remainder of the previous call is added, the result is rounded to the
    nearest integer, and the new remainder is kept (source:
    driver/accel.c:340-352, driver/FixedMath/Fixed64.h:132-134).

Written as one formula, for `RatioYX == 1`, no rotation and no snapping
(inferred from the steps above), with `k` the directional multiplier of each
axis:

    out_x = dx * kx * PreScale * min(OutputCap, Sensitivity * f(v))
    v     = min(InputCap, PreScale * sqrt((kx dx)^2 + (ky dy)^2) / ms) - Offset

Note: a tooltip in the GUI says "Rotation is applied after Angle Snapping"
(source: gui/main.cpp:486); the driver code does the opposite (source:
driver/accel.c:252, driver/accel.c:323).

### 1.5 Formula and parameters per mode

`A` = `Acceleration`, `E` = `Exponent`, `M` = `Midpoint`, `V` = `Motivity`,
`S` = `UseSmoothing`. All formulas return the multiplier `f(v)`.

**Linear (1)** (source: driver/accel_modes.c:393-404, driver/accel_modes.c:50-74)

- `S = 0`: `f = 1 + A v`.
- `S = 1`: `cap_y = |M - 1|`, `sign = -1` if `M < 1` else `1`,
  `cap_x = (cap_y / 2) / A`, `C = -cap_y cap_x / 2`.
  `f = 1 + sign A v` for `v < cap_x`, else `f = 1 + sign (C / v + cap_y)`.
  `M` is the output limit (GUI label "Output Limit", source: gui/main.cpp:232).
- Rejected: `A == 0` (source: driver/accel_modes.c:51-55).

**Power (2)** (source: driver/accel_modes.c:406-431, driver/accel_modes.c:155-216)

- `off_x = (M / (E + 1))^(1/E) / A`, `P = off_x M E / (E + 1)`; both 0 when
  `M == 0`.
- `f = M` for `v <= off_x`.
- `S = 0`: `f = (A v)^E + P / v`.
- `S = 1`: `cap_y = V`, `cap_x = (V / (E + 1))^(1/E) / A`,
  `G = (A cap_x)^E cap_x + P - cap_x cap_y`.
  `f = (A v)^E + P / v` for `v < cap_x`, else `f = cap_y + G / v`.
- `M` is the output offset, `V` the output limit (GUI labels, source:
  gui/main.cpp:249, gui/main.cpp:259). No 1 is added in this mode.
- Rejected: `E == 0`, `E == -1`, `A == 0`; `M >= V` with `S = 1`;
  `M / (A E) > 100` (source: driver/accel_modes.c:156-174).

**Classic (3)** (source: driver/accel_modes.c:433-461, driver/accel_modes.c:77-106)

- `S = 0`: `f = 1 + (A v)^(E - 1)`.
- `S = 1`: `cap_y = |M - 1|`, `sign` as in Linear,
  `cap_x = (cap_y / E)^(1/(E - 1)) / A`, `C = -cap_y cap_x (E - 1) / E`.
  `f = 1 + sign (A v)^(E - 1)` for `v < cap_x`, else
  `f = 1 + sign (C / v + cap_y)`.
- Rejected: `S = 1` with `E == 0` or `E == 1` (source:
  driver/accel_modes.c:78-81).

**Motivity (4)** (source: driver/accel_modes.c:463-475)

- `f = 1 + (A - 1) / (1 + exp(M - v))`. `S`, `E` and `V` are not used.
- Despite the name this is a plain logistic curve over the speed, not
  RawAccel's Motivity (inferred, see 3.2).

**Synchronous (5)** (source: driver/accel_modes.c:23-47,
driver/accel_modes.c:287-307, driver/accel_modes.c:477-493)

- Parameter roles: `V` motivity, `E` gamma, `A` sync speed, `M` smoothness
  (source: gui/FunctionHelper.cpp:16-20, gui/main.cpp:310-316).
- `g = E / ln V`, `sharp = 16` if `M == 0` else `0.5 / M`.
- "Legacy" function `L(v)` for `sharp < 16`: `f = 1` at `v == A`; otherwise
  `d = ln v - ln A`, `t = tanh((g |d|)^sharp)^(1/sharp)`,
  `f = exp(sign(d) t ln V)`.
- For `sharp >= 16` (that is `M == 0` or `M <= 0.03125`) the kernel code
  returns `1/V` when `g d < 1` and `V` otherwise (source:
  driver/accel_modes.c:288-293). The GUI preview of the same branch clamps
  `g d` to [-1, 1] and returns `exp(g d ln V)` (source:
  gui/FunctionHelper.cpp:33-38). The two differ; the kernel version is a step
  (inferred, this looks like a bug in the kernel branch).
- `S = 0`: `f = L(v)`. `S = 1`: `f = I(v) / v`, where `I` is a table of the
  running integral of `L`, built with 8 points per power of two from 2^-3 to
  2^9 and interpolated linearly (source: driver/accel_modes.c:276-391).
- Rejected: `V <= 1` (source: driver/accel_modes.c:24-28).

**Natural (6)** (source: driver/accel_modes.c:531-554, driver/accel_modes.c:109-124)

- `lim = E - 1`, `a = A / |lim|`, `f = 1` for `v <= M`.
- For `v > M`, with `o = M - v` and `d = exp(a o)`:
  - `S = 0`: `f = 1 + lim (1 - (M - d o) / v)`.
  - `S = 1`: `f = 1 + (lim (d / a - o) - lim / a) / v`.
- `A` is the decay rate, `M` the offset, `E` the limit (GUI labels, source:
  gui/main.cpp:334-338).
- Rejected: `E == 1`, `A == 0` (source: driver/accel_modes.c:110-119).

**Jump (7)** (source: driver/accel_modes.c:496-529, driver/accel_modes.c:127-152)

- `r = 0` if `E M < 1`, else `r = 2 pi / (E M)`. `D = exp(r (M - v))`.
- `S = 0`: `f = 1 + (A - 1) / (1 + D)` if `r != 0`; with `r == 0`: `f = 1` for
  `v <= M`, else `f = A`.
- `S = 1`: if `r != 0`:
  `f = 1 + ((A - 1)(v + ln(1 + D) / r) - C0) / v` with
  `C0 = (A - 1) ln(1 + exp(r M)) / r`; with `r == 0`: `f = 1` for `v <= M`,
  else `f = 1 + (A - 1)(v - M) / v`.
- `A` is the multiplier after the jump, `M` the speed of the jump, `E` the
  smoothness of the jump (GUI labels, source: gui/main.cpp:356-360). Here
  `UseSmoothing` does not smooth the jump; it selects the second formula
  (inferred).
- Rejected: `M == 0` (source: driver/accel_modes.c:128-133).

**LUT (8) and Custom Curve (9)** (source: driver/accel_modes.c:560-593)

- Points `(x_i, y_i)`, `y` is the multiplier itself. `f = y_0` below `x_0`;
  otherwise linear interpolation between the two neighbouring points; beyond
  the last point the last segment is continued (source:
  driver/accel_modes.c:563-589, driver/FixedMath/Fixed64.h:272-274).
- At most 128 points and 4096 characters (source: driver/accel_modes.h:14-15).
- The mode falls back to 0 when there are fewer than 2 points, when the last
  two points are equal, or when the x values are not sorted (source:
  driver/accel.c:159-164, driver/accel_modes.c:219-231).

Ranges: apart from the rejections listed above and the angle snap threshold,
the driver checks no ranges (inferred from driver/accel.c:108-172 and
driver/accel_modes.c:13-274). The GUI sliders use these ranges, and Ctrl+click
allows any value (source: gui/main.cpp:223):

| Setting | Slider range | Source |
|---|---|---|
| Sensitivity, Ratio Y/X | 0.005 to 5 | gui/main.cpp:175-178 |
| Output Cap | 0 to 5 | gui/main.cpp:179 |
| Input Cap | 0 to 120 | gui/main.cpp:180 |
| Offset | -50 to 50 | gui/main.cpp:181 |
| Pre-Scale | 0.01 to 10 | gui/main.cpp:182 |
| Linear: Acceleration / Output Limit | 0 to 1 / 0.1 to 10 | gui/main.cpp:221, gui/main.cpp:232 |
| Power: Acceleration / Exponent / Output Offset / Output Limit | 0.001 to 5 / 0.01 to 1 / 0 to 5 / 0.1 to 10 | gui/main.cpp:246-259 |
| Classic: Acceleration / Exponent / Output Limit | 0.001 to 2 / 2.01 to 5 / 0.1 to 10 | gui/main.cpp:271-282 |
| Motivity: Acceleration / Midpoint | 0.01 to 10 / 0.1 to 50 | gui/main.cpp:296-298 |
| Synchronous: Gamma / Smoothness / Motivity / SyncSpeed | 0.1 to 20 / 0.1 to 20 / 1 to 10 / 0.01 to 10 | gui/main.cpp:310-316 |
| Natural: Decay Rate / Midpoint / Limit | 0.001 to 5 / 0 to 50 / 0.01 to 8 | gui/main.cpp:334-338 |
| Jump: Acceleration / Midpoint / Smoothness | 0 to 10 / 0.1 to 50 / 0 to 1 | gui/main.cpp:356-360 |
| Rotation, snap threshold, snap angle (degrees) | -180 to 180, 0 to 179.99, 0 to 179.99 | gui/main.cpp:479-483 |
| Directional scales | 0.1 to 5 | gui/main.cpp:489-492 |

The GUI has its own start values that differ from the driver defaults
(`accel` 2.0, `exponent` 0.4, `midpoint` 5.0) (source:
gui/DriverHelper.h:201-203).

### 1.6 Config file, tools, GUI

- The persistent file is `/etc/yeetmouse.conf` (source: README.org:42-52). It
  is installed from `install_files/yeetmouse.conf.sample` only when it does not
  exist yet (source: Makefile:151-159).
- Format: one `key=value` per line (source: gui/ConfigHelper.h:64-66,
  install_files/yeetmouse.conf.sample:1-22). Keys as written by the tools
  (source: gui/ConfigHelper.cpp:54-75):

  `sens`, `ratioYX`, `outCap`, `inCap`, `offset`, `accel`, `exponent`,
  `midpoint`, `motivity`, `preScale`, `accelMode`, `useSmoothing`, `rotation`,
  `as_threshold`, `as_angle`, `dirScaleXPos`, `dirScaleXNeg`, `dirScaleYPos`,
  `dirScaleYNeg`, `LUT_size`, `LUT_data`, `CC_data_aggregate`.

- Keys are compared in lower case and have aliases (source:
  gui/ConfigHelper.h:65, gui/ConfigHelper.h:78-133).
- `accelMode` holds the enum string, for example `AccelMode_Linear` (source:
  gui/ConfigHelper.cpp:64, install_files/yeetmouse.conf.sample:11). A number is
  accepted as an old format, and numbers above 4 are shifted by 2 (source:
  gui/ConfigHelper.h:99-108). So a number must not be written there (inferred).
- `rotation`, `as_threshold` and `as_angle` are in **degrees** in the config
  file and in **radians** in sysfs (source: gui/ConfigHelper.cpp:66-68,
  gui/ConfigHelper.h:112-117, gui/DriverHelper.cpp:417-419,
  gui/DriverHelper.cpp:337-342).
- A file is refused when all lines are unknown, or when fewer than 14 lines
  were read and more than 3 are unknown (source: gui/ConfigHelper.h:143-146).
- The tool is `yeetmousectl`, installed as `/usr/bin/yeetmousectl` (source:
  Makefile:131-134). Commands (source: tools/yeetmousectl/main.cpp:50-52):
  - `apply <config>`: parse the file, write every parameter to sysfs, then
    write 1 to `update` (source: tools/yeetmousectl/main.cpp:9-34,
    gui/DriverHelper.cpp:390-439).
  - `dump`: read sysfs and print the config text (source:
    tools/yeetmousectl/main.cpp:36-44).
  - `save <file>`: the same text into a file (source:
    tools/yeetmousectl/main.cpp:76-91).
- The file is applied at boot by `yeetmouse.service`, which waits for the
  `update` parameter to appear, changes the group of all parameter files to
  `yeetmouse`, and runs `/usr/bin/yeetmousectl apply /etc/yeetmouse.conf`
  (source: install_files/systemd/yeetmouse.service:5-11). The file is not read
  by the kernel module itself (inferred).
- The GUI is built as `gui/YeetMouseGui` (source: gui/Makefile:34) and
  installed as `/usr/bin/yeetmouse` (source: Makefile:13-14, Makefile:99), with
  a desktop entry that starts that path (source:
  install_files/yeetmouse.desktop:3).
- GUI "Apply": write all parameters and `update` (source: gui/main.cpp:1139-1140).
  "Apply + Save": write all parameters without `update`, run
  `pkexec /usr/bin/yeetmousectl save /etc/yeetmouse.conf`, then write `update`
  (source: gui/main.cpp:1152-1157, gui/DriverHelper.cpp:171). The GUI blocks
  both buttons for 1.1 s after a click because of the one second limit (source:
  gui/main.cpp:1133-1135).
- Oddity when reading back: the tools treat the Y ratio as "unused" when
  `RatioYX` equals `Sensitivity`, and then write `ratioYX=1` (source:
  gui/DriverHelper.cpp:366, gui/ConfigHelper.cpp:55). With for example
  `Sensitivity` 0.5 and `RatioYX` 0.5, `dump` and `save` would lose the ratio
  (inferred).
- The README's section on converting RawAccel settings says Natural is "not
  implemented" (source: README.org:226); the driver code implements it (source:
  driver/accel_modes.c:531-554). The README is out of date there (inferred).

---

## 2. RawAccel 1.7.1, and where 1.6.x differs

### 2.1 Settings file and top-level keys

- The settings are one JSON object with `version`, `defaultDeviceConfig`,
  `profiles` and `devices` (source: wrapper/wrapper.cpp:654-668 (v1.7.1)).
  RawAccel adds two comment properties in front, `### Accel modes ###` and
  `### Cap modes ###` (source: wrapper/wrapper.cpp:793-794 (v1.7.1)).
- `writer.exe <file>` reads the file, validates it and sends it to the driver;
  on an error it shows a message box and exits with status 1 (source:
  writer/Program.cs:19-23, writer/Program.cs:51-59 (v1.7.1)).
- All properties of a profile are required (source: wrapper/wrapper.cpp:208
  (v1.7.1)).
- Validation checks every field of the acceleration arguments, whatever the
  selected mode. For example `motivity <= 1` is an error in a `classic`
  profile too (source: common/rawaccel-validate.hpp:38-131 (v1.7.1)). In
  "whole" mode only the X arguments are checked (source:
  common/rawaccel-validate.hpp:133-139 (v1.7.1)).
- The driver delays applying new settings by 1000 ms (source:
  common/rawaccel-base.hpp:14 (v1.7.1)).

### 2.2 Profile keys

Exact JSON strings in 1.7.1 (source: wrapper/wrapper.cpp:208-245 (v1.7.1)):

| JSON key | Native field | Default |
|---|---|---|
| `name` | `name` | `default` |
| `Stretches domain for horizontal vs vertical inputs` (`x`, `y`) | `domain_weights` | 1, 1 |
| `Stretches accel range for horizontal vs vertical inputs` (`x`, `y`) | `range_weights` | 1, 1 |
| `Whole or horizontal accel parameters` | `accel_x` | see 2.3 |
| `Vertical accel parameters` | `accel_y` | see 2.3 |
| `Input speed calculation parameters` | `speed_processor_args` | see below |
| `Output DPI` | `output_dpi` | 1000 |
| `Y/X output DPI ratio (vertical sens multiplier)` | `yx_output_dpi_ratio` | 1 |
| `L/R output DPI ratio (left sens multiplier)` | `lr_output_dpi_ratio` | 1 |
| `U/D output DPI ratio (up sens multiplier)` | `ud_output_dpi_ratio` | 1 |
| `Degrees of rotation` | `degrees_rotation` | 0 |
| `Degrees of angle snapping` | `degrees_snap` | 0 |
| `Input Speed Cap` | `speed_max` | 0 |

Defaults (source: common/rawaccel-base.hpp:77-98 (v1.7.1)). `speed_min` exists
natively but is not part of the JSON (source: wrapper/wrapper.cpp:242-243
(v1.7.1)).

Keys inside `Input speed calculation parameters` (source:
wrapper/wrapper.cpp:190-206, common/rawaccel-base.hpp:68-75 (v1.7.1)):

| JSON key | Default |
|---|---|
| `Whole/combined accel (set false for 'by component' mode)` | true |
| `lpNorm` | 2 |
| `Time in ms after which an input is weighted at half its original value.` | 0 |
| `Time in ms after which scale is weighted at half its original value.` | 0 |
| `Time in ms after which an output is weighted at half its original value.` | 0 |

Keys of `defaultDeviceConfig` and of the `config` of each entry in `devices`
(source: wrapper/wrapper.cpp:256-282 (v1.7.1)): `disable`, `setExtraInfo`
(optional), `Use constant time interval based on polling rate` (optional),
`DPI (normalizes input speed unit: counts/ms -> in/s)`,
`Polling rate Hz (keep at 0 for automatic adjustment)`, `minimumTime`
(optional), `maximumTime` (optional). Defaults: DPI 0, polling rate 0, minimum
time 0.0625 ms, maximum time 100 ms (source: common/rawaccel.hpp:7-19,
common/rawaccel-base.hpp:8-12 (v1.7.1)).

### 2.3 Keys of one set of acceleration arguments

The two objects `Whole or horizontal accel parameters` and
`Vertical accel parameters` have the same keys (source:
wrapper/wrapper.cpp:58-91 (v1.7.1)); defaults (source:
common/rawaccel-base.hpp:44-66 (v1.7.1)):

| JSON key | Native field | Default | Used by mode |
|---|---|---|---|
| `mode` | `mode` | `noaccel` | all |
| `Gain / Velocity` | `gain` | true | all except `noaccel` |
| `inputOffset` | `input_offset` | 0 | classic, natural |
| `outputOffset` | `output_offset` | 0 | power |
| `acceleration` | `acceleration` | 0.005 | classic |
| `decayRate` | `decay_rate` | 0.1 | natural |
| `gamma` | `gamma` | 1 | synchronous |
| `motivity` | `motivity` | 1.5 | synchronous |
| `exponentClassic` | `exponent_classic` | 2 | classic |
| `scale` | `scale` | 1 | power |
| `exponentPower` | `exponent_power` | 0.05 | power |
| `limit` | `limit` | 1.5 | natural |
| `syncSpeed` | `sync_speed` | 5 | synchronous |
| `smooth` | `smooth` | 0.5 | synchronous, jump |
| `Cap / Jump` (`x`, `y`) | `cap` | 15, 1.5 | classic, power, jump |
| `Cap mode` | `cap_mode` | `output` | classic, power |
| `data` | `data` | empty | lut (flat list `x1, y1, x2, y2, ...`) |

- Which field a mode uses was read from the mode files (source:
  common/accel-classic.hpp:12-153, common/accel-jump.hpp:14-25,
  common/accel-natural.hpp:13-18, common/accel-power.hpp:14-40,
  common/accel-synchronous.hpp:21-30, common/accel-lookup.hpp:45-54 (v1.7.1)).
- Mode strings: `classic`, `jump`, `natural`, `synchronous`, `power`, `lut`,
  `noaccel` (source: wrapper/wrapper.cpp:37-41 (v1.7.1)). There is no `linear`
  mode in the file; the Guide's "Linear" style is `classic` with exponent 2
  (inferred from doc/Guide.md:154-158 (v1.7.1) and
  common/accel-classic.hpp:12-15 (v1.7.1)).
- Cap mode strings: `in_out`, `input`, `output` (source:
  wrapper/wrapper.cpp:43-47 (v1.7.1)).
- For every mode other than `lut` RawAccel writes `data` as an empty list
  (source: wrapper/wrapper.cpp:763-787 (v1.7.1)).
- Validation limits (source: common/rawaccel-validate.hpp:53-129 (v1.7.1)):
  offsets >= 0; cap x and y >= 0, and > 0 for jump and for `in_out`;
  `acceleration`, `scale`, `gamma`, `decayRate`, `exponentPower`, `limit`,
  `syncSpeed` > 0; `motivity` > 1; `exponentClassic` > 1; `smooth` in [0, 1];
  lut needs 2 to 257 points.
- Profile limits (source: common/rawaccel-validate.hpp:141-179 (v1.7.1)):
  snap in [0, 45] degrees; output DPI and Y/X ratio not 0; L/R and U/D ratio
  > 0; domain weights > 0; range weights >= 0; lpNorm > 0; speed cap >= 0.

### 2.4 Per axis or combined

- "Whole" (key true): one speed is computed from both axes and one multiplier
  is applied to both; only the X arguments are used (source:
  common/rawaccel.hpp:380-397 (v1.7.1)).
- "By component" (key false): X and Y each get their own speed and their own
  curve from the X and Y arguments (source: common/rawaccel.hpp:358-372
  (v1.7.1)).
- In whole mode the speed is the Euclidean length when `lpNorm` is 2, the
  larger component when `lpNorm` is 16 or more, and the Lp norm otherwise
  (source: common/rawaccel.hpp:199-210, common/rawaccel.hpp:235-255,
  common/rawaccel-base.hpp:22 (v1.7.1)).
- Domain weights multiply the X and Y speed components before the norm; range
  weights scale the distance of the multiplier from 1, blended by the angle of
  the movement when they differ (source: common/rawaccel.hpp:353-356,
  common/rawaccel.hpp:383-388, common/rawaccel.hpp:451 (v1.7.1)).

### 2.5 The pipeline of `modify()`

Order in 1.7.1 (source: common/rawaccel.hpp:313-423 (v1.7.1)):

1. `ips_factor = dpi_factor / time` (line 320). `dpi_factor` is `1000 / DPI`
   when the device DPI is set, otherwise 1 (source: driver/driver.cpp:376,
   common/rawaccel-base.hpp:24-25 (v1.7.1)).
2. Rotation by `Degrees of rotation`, same matrix as the fork (line 322;
   source: common/math-vec2.hpp:14-26 (v1.7.1)).
3. Angle snapping: within `snap` degrees of the X or the Y axis the vector is
   put on that axis, length kept (lines 324-344).
4. Speed clamp, only when `Input Speed Cap > 0`: the input vector itself is
   shortened so that its speed does not exceed the cap (lines 346-351; flag at
   common/rawaccel.hpp:46 (v1.7.1)).
5. `speed = |in * ips_factor * domain_weights|` (lines 353-356).
6. Curve: `scale = 1 + (f(speed) - 1) * range_weight`; `in *= scale` (lines
   380-397 and 451). In 1.7 the speed, the scale and the output can each be
   smoothed over time when a halflife is set (lines 235-255, 392-409).
7. Output scale: `in.x *= output_dpi / 1000 * dpi_factor`,
   `in.y *= output_dpi / 1000 * dpi_factor * yx_ratio` (lines 412-414, 429).
8. **Directional multipliers**: `if in.x < 0: in.x *= lr_ratio`,
   `if in.y < 0: in.y *= ud_ratio` (lines 416-422).

After `modify()` the driver adds the remainder of the previous packet and
truncates toward zero (source: driver/driver.cpp:115-127 (v1.7.1)).

So in RawAccel all four directional numbers (output DPI, Y/X, L/R, U/D) act
after the curve and do not change the speed the curve sees (inferred from the
order above).

Time and DPI:

- `time` is the measured time since the previous callback divided by the
  number of packets in the callback, clamped to [minimum, maximum] (source:
  driver/driver.cpp:84-94 (v1.7.1)).
- In 1.7.1 a polling rate > 0 raises the minimum to `1000 / rate`; only with
  `Use constant time interval based on polling rate` also set is the time fixed
  to `1000 / rate` (source: driver/driver.cpp:378-392 (v1.7.1)).
- With DPI 0 the curve sees counts per millisecond. With DPI `d` it sees
  `counts * (1000 / d) / ms`; "At this DPI, one count per ms equals one inch
  per second" (source: common/rawaccel-base.hpp:24-25 (v1.7.1)). The same
  factor `1000 / d` also multiplies the output (line 412), so a mouse with its
  DPI entered behaves like a 1000 DPI mouse (source: doc/Guide.md:138-139
  (v1.7.1)).
- `Output DPI` 1000 is a final factor of 1 (source: common/rawaccel.hpp:429
  (v1.7.1)).

### 2.6 Gain switch and cap modes

- `Gain / Velocity` selects between two implementations of each mode. With
  false ("legacy") the formula describes the multiplier directly; with true the
  formula is applied so that the slope of the output speed (the gain) has the
  described shape (source: common/accel-union.hpp:28-45, doc/Guide.md:108-109
  (v1.7.1)).
- For `lut` the same flag means: the `y` values are output speeds (true) or
  multipliers (false) (source: common/accel-lookup.hpp:53,
  common/accel-lookup.hpp:77-80 (v1.7.1)).
- Cap modes for classic and power (source: common/accel-classic.hpp:34-68,
  common/accel-classic.hpp:88-136, common/accel-power.hpp:14-40,
  common/accel-power.hpp:83-94, common/accel-power.hpp:112-143 (v1.7.1)):
  - `output`: `cap.y` is the limit; `cap.y == 0` means no cap; `acceleration`
    or `scale` is used.
  - `input`: `cap.x` is the speed at which the cap starts; `cap.x == 0` means
    no cap.
  - `in_out`: the curve is forced through the point (`cap.x`, `cap.y`);
    `acceleration` or `scale` is then computed and the value in the file is
    ignored.
- Legacy cap: the multiplier is cut off at the cap. Gain cap: beyond the cap
  point the gain stays constant, so the multiplier keeps approaching the limit
  as `cap_y + constant / v` (source: common/accel-classic.hpp:71-75,
  common/accel-classic.hpp:139-153 (v1.7.1)).

### 2.7 Formula per mode

`x` is the speed, `o` the input offset.

**classic** (source: common/accel-classic.hpp:12-153 (v1.7.1)); `a` =
`acceleration`, `n` = `exponentClassic`

- Base: `b(x) = a^(n-1) (x - o)^n / x`. `f = 1` for `x <= o`.
- Legacy: `f = 1 + sign * min(b(x), cap)`; with cap mode `output`,
  `cap = |cap.y - 1|` and `sign = -1` when `cap.y < 1`.
- Gain, cap mode `output`, `cap.y > 0`: `cy = |cap.y - 1|`,
  `cx = (a o + (cy / n)^(1/(n-1))) / a`, `C = (b(cx) - cy) cx`.
  `f = 1 + sign b(x)` for `x < cx`, else `f = 1 + sign (C / x + cy)`.
- Without a cap both variants are `f = 1 + b(x)` (inferred from the two
  operators).

**power** (source: common/accel-power.hpp:14-156 (v1.7.1)); `s` = `scale`,
`n` = `exponentPower`, `q` = `outputOffset`

- `ox = (q / (n + 1))^(1/n) / s`, `P = ox q n / (n + 1)`.
- Base: `b(x) = q` for `x <= ox`, else `(s x)^n + P / x`.
- Legacy: `f = min(b(x), cap)`.
- Gain, cap mode `output`, `cap.y > 0`: `cx = (cap.y / (n + 1))^(1/n) / s`,
  `B = (b(cx) - cap.y) cx`; `f = b(x)` for `x < cx`, else `f = cap.y + B / x`.

**natural** (source: common/accel-natural.hpp:13-55 (v1.7.1)); `d` =
`decayRate`, `L` = `limit - 1`, `a = d / |L|`

- `f = 1` for `x <= o`. With `u = o - x`, `e = exp(a u)`:
- Legacy: `f = 1 + L (1 - (o - e u) / x)`.
- Gain: `f = 1 + (L (e / a - u) - L / a) / x`.

**jump** (source: common/accel-jump.hpp:14-79 (v1.7.1)); step at `cap.x`,
height `h = cap.y - 1`

- `r = 0` if `smooth * cap.x < 1`, else `r = 2 pi / (smooth * cap.x)`.
- Legacy: `f = 1 + h / (1 + exp(r (cap.x - x)))` if `r != 0`; else `f = 1` for
  `x < cap.x` and `1 + h` otherwise.
- Gain: with `F(x) = h (x + ln(1 + exp(r (cap.x - x))) / r)`:
  `f = 1 + (F(x) - F(0)) / x` if `r != 0`; else `f = 1` for `x < cap.x` and
  `1 + h (x - cap.x) / x` otherwise.

**synchronous** (1.7 only) (source: common/accel-synchronous.hpp:21-131
(v1.7.1))

- `g = gamma / ln(motivity)`, `sharp = 16` if `smooth == 0` else
  `0.5 / smooth`.
- Legacy, `sharp >= 16`: `c = clamp(g (ln x - ln syncSpeed), -1, 1)`,
  `f = exp(c ln motivity)`.
- Legacy, `sharp < 16`: `f = 1` at `x == syncSpeed`; otherwise
  `dl = ln x - ln syncSpeed`, `t = tanh((g |dl|)^sharp)^(1/sharp)`,
  `f = exp(sign(dl) t ln motivity)`.
- Gain: a table of the running integral of the legacy function (8 points per
  power of two from 2^-3 to 2^9, stored as 32 bit floats), `f = I(x) / x`.

**lut** (source: common/accel-lookup.hpp:45-98 (v1.7.1))

- Points `(x_i, y_i)`; linear interpolation of `y`, continued beyond the last
  point; below the first point the value of the first point is used. With the
  gain flag true the result is divided by `x` (the `y` are output speeds).

**noaccel**: `f = 1` (source: common/accel-noaccel.hpp:8-14 (v1.7.1)).

### 2.8 Differences in 1.6.1

| Topic | 1.6.1 | 1.7.1 | Source |
|---|---|---|---|
| Mode name | `motivity` | `synchronous` (same position in the list) | wrapper/wrapper.cpp:40 (v1.6.1), wrapper/wrapper.cpp:40 (v1.7.1) |
| Args key | `growthRate` | `gamma` | wrapper/wrapper.cpp:71 (v1.6.1), wrapper/wrapper.cpp:72 (v1.7.1) |
| Args key | `midpoint` | `syncSpeed` | wrapper/wrapper.cpp:77 (v1.6.1), wrapper/wrapper.cpp:78 (v1.7.1) |
| Whole flag and `lpNorm` | directly in the profile | inside `Input speed calculation parameters` | wrapper/wrapper.cpp:108-112 (v1.6.1), wrapper/wrapper.cpp:190-196 (v1.7.1) |
| Smoothing halflife keys | do not exist | three keys | wrapper/wrapper.cpp:198-205 (v1.7.1) |
| Sensitivity | `Sensitivity multiplier`, 1 = neutral | `Output DPI`, 1000 = neutral | wrapper/wrapper.cpp:124-125 (v1.6.1), wrapper/wrapper.cpp:227-228 (v1.7.1) |
| Ratios | `Y/X sensitivity ratio (vertical sens multiplier)`, `L/R sensitivity ratio (left sens multiplier)`, `U/D sensitivity ratio (up sens multiplier)` | the `... output DPI ratio ...` keys of 2.2 | wrapper/wrapper.cpp:126-131 (v1.6.1) |
| Device DPI key | `DPI (normalizes sens to 1000dpi and converts input speed unit: counts/ms -> in/s)` | `DPI (normalizes input speed unit: counts/ms -> in/s)` | wrapper/wrapper.cpp:163 (v1.6.1), wrapper/wrapper.cpp:270 (v1.7.1) |
| Constant time key | does not exist | `Use constant time interval based on polling rate` | wrapper/wrapper.cpp:267 (v1.7.1) |
| Polling rate > 0 | time fixed to `1000 / rate` | only a lower limit, unless the constant time key is set | driver/driver.cpp:364-372 (v1.6.1), driver/driver.cpp:378-392 (v1.7.1) |
| Maximum time default | 16 ms | 100 ms | common/rawaccel-base.hpp:12 (v1.6.1), common/rawaccel-base.hpp:12 (v1.7.1) |
| Output factor | `sensitivity * dpi_factor` | `output_dpi / 1000 * dpi_factor` | common/rawaccel.hpp:186-188 (v1.6.1), common/rawaccel.hpp:412-414 (v1.7.1) |

- The order of `modify()` is the same in 1.6.1: rotate, snap, speed clamp,
  curve, sensitivity and Y/X, then L/R and U/D (source:
  common/rawaccel.hpp:112-197 (v1.6.1)).
- The files for classic, jump, natural, lookup and noaccel are identical in
  both tags, and power differs only in the name of one helper function
  (inferred from a line by line comparison of the files of both tags).
- The 1.6 `motivity` mode has its own formula (source:
  common/accel-motivity.hpp:16-26 (v1.6.1)): with `G = exp(growthRate)`,
  `f = exp(2 ln(motivity) / (1 + exp(G (ln midpoint - ln x))) - ln(motivity))`.
  Its gain variant uses the same kind of integral table as synchronous (source:
  common/accel-motivity.hpp:38-61 (v1.6.1)). The file
  `common/accel-motivity.hpp` does not exist at v1.7.1 and
  `common/accel-synchronous.hpp` does not exist at v1.6.1 (inferred: both
  downloads returned "404: Not Found").
- The 1.6 formula equals `exp(ln(motivity) tanh((G / 2) ln(x / midpoint)))`,
  which is the 1.7 synchronous legacy formula with `smooth = 0.5`,
  `syncSpeed = midpoint` and `gamma = ln(motivity) * exp(growthRate) / 2`
  (inferred: algebra, agreed to 1e-15 in the numeric check).

---

## 3. Mapping

### 3.1 Units and the surrounding pipeline

| Topic | yeetmouse fork | RawAccel | Verdict |
|---|---|---|---|
| Speed unit | counts per ms, measured time (source: driver/accel.c:209, driver/accel.c:238-239) | counts per ms, measured time, times `1000 / DPI` if a DPI is set (source: common/rawaccel.hpp:320, common/rawaccel.hpp:353-356 (v1.7.1)) | Same unit when DPI is 0 and `PreScale` is 1, so curve parameters carry over without conversion (inferred) |
| DPI normalisation | `PreScale` scales speed and output (source: driver/accel.c:232-235) | `1000 / DPI` scales speed and output (source: common/rawaccel.hpp:320, common/rawaccel.hpp:412 (v1.7.1)) | Same structure: `PreScale = 1000 / DPI` (inferred). The fork's GUI tooltip suggests `800 / DPI` (source: gui/main.cpp:194), a different reference DPI |
| Time limits | upper limit 100 ms, no lower limit, one time stamp for all devices (source: driver/accel.c:185, driver/accel.c:215). (corrected) The time difference in nanoseconds goes through a 32-bit conversion before the 100 ms limit is applied, see 8.4 | [0.0625 ms or `1000 / rate`, 100 ms] in 1.7.1, per device, divided by packet count (source: driver/driver.cpp:84-94, driver/driver.cpp:378-392 (v1.7.1)) | Similar, not identical; no counterpart for the constant time option (inferred) |
| Final sensitivity | `Sensitivity` (source: driver/accel.c:297-298) | `Output DPI / 1000` (1.7), `Sensitivity multiplier` (1.6) | Identical after that conversion (inferred) |
| Y/X ratio | `RatioYX` (source: driver/accel.c:309) | Y/X ratio key | Identical (inferred) |
| Rotation | radians in sysfs, degrees in the config file (source: gui/DriverHelper.cpp:417) | degrees (source: common/math-vec2.hpp:14-18 (v1.7.1)) | Same matrix; identical only while the four directional numbers are neutral, because the fork scales by direction before rotating and RawAccel after (inferred) |
| Angle snapping | one freely chosen line, applied to the output (source: driver/accel.c:323-338) | both axes, applied to the input (source: common/rawaccel.hpp:324-344 (v1.7.1)) | Different features, no mapping (inferred) |
| Input speed limit | `InputCap` limits the curve argument only (source: driver/accel.c:242-247) | `Input Speed Cap` shortens the movement itself (source: common/rawaccel.hpp:346-351 (v1.7.1)) | Different, no mapping (inferred) |
| Output limit | `OutputCap` limits `Sensitivity * f` (source: driver/accel.c:301-302) | none at profile level; caps exist per mode | No general mapping (inferred) |
| Offset | `Offset` shifts the curve: `f(v - Offset)` (source: driver/accel.c:249) | `inputOffset`, used only by classic and natural, and inside the formula (source: common/accel-classic.hpp:14, common/accel-natural.hpp:14 (v1.7.1)) | Different for classic (example: `a` 0.05, `n` 2, offset 10, speed 20 gives 1.50 in the fork and 1.25 in RawAccel; inferred, computed from the formulas). For natural, RawAccel's offset is the fork's `Midpoint`, not `Offset` |
| Rounding | nearest, remainder carried (source: driver/accel.c:347-352) | toward zero, remainder carried (source: driver/driver.cpp:115-127 (v1.7.1)) | Same long-run sum, single reports can differ by one count (inferred) |
| Smoothing over time | none | three halflife settings in 1.7 (source: common/rawaccel-base.hpp:68-75 (v1.7.1)) | RawAccel only |
| Whole or by component, Lp norm, domain and range weights | always whole, Euclidean (source: driver/accel.c:238) | selectable (source: common/rawaccel.hpp:199-210 (v1.7.1)) | RawAccel only; the fork matches whole, `lpNorm` 2, weights 1 (inferred) |

### 3.2 Mode by mode

(corrected) "Identical" below is about the formulas as real functions. The
fork computes them in Q32.32 fixed point; how far that is from RawAccel's
doubles was measured and is given in 8.6. The six modes Off, Linear, Classic,
Power, Natural and Jump agree as real functions to about 5e-16 relative (Jump
with gain 6e-9) (verified numerically).

"Identical" below always assumes: fork `Offset` 0, `InputCap` 0, `OutputCap` 0;
RawAccel whole mode, `lpNorm` 2, weights 1, no speed cap, no smoothing
halflife; and matching DPI handling as in 3.1 (inferred).

| Fork mode | RawAccel mode | Parameter conversion | Verdict |
|---|---|---|---|
| 0 Current | `noaccel` | none | Identical: multiplier 1 on both sides (source: driver/accel.c:286-288; common/accel-noaccel.hpp:13 (v1.7.1)) |
| 1 Linear, `UseSmoothing` 0 | `classic`, `exponentClassic` 2, `inputOffset` 0, cap mode `output`, `cap.y` 0 | `acceleration = Acceleration` | Identical: `1 + a v` (inferred from 1.5 and 2.7) |
| 1 Linear, `UseSmoothing` 1 | same, `Gain / Velocity` true, `cap.y > 0` | `acceleration = Acceleration`, `cap.y = Midpoint` | Identical, same cap point and constant (inferred; numeric check agreed to 1e-16) |
| 3 Classic, `UseSmoothing` 0 | `classic`, `inputOffset` 0, cap mode `output`, `cap.y` 0 | `acceleration = Acceleration`, `exponentClassic = Exponent` | Identical: `1 + (a v)^(n-1)` (inferred) |
| 3 Classic, `UseSmoothing` 1 | same, `Gain / Velocity` true, `cap.y > 0` | as above, `cap.y = Midpoint` | Identical (inferred; numeric check agreed to 1e-16) |
| 2 Power, `UseSmoothing` 0 | `power`, cap mode `output`, `cap.y` 0 | `scale = Acceleration`, `exponentPower = Exponent`, `outputOffset = Midpoint` | Identical (inferred) |
| 2 Power, `UseSmoothing` 1 | same, `Gain / Velocity` true, `cap.y > 0` | as above, `cap.y = Motivity` | Identical (inferred) |
| 6 Natural | `natural` | `decayRate = Acceleration`, `limit = Exponent`, `inputOffset = Midpoint`, `Gain / Velocity = UseSmoothing` | Identical (inferred) |
| 7 Jump | `jump` | `cap.x = Midpoint`, `cap.y = Acceleration`, `smooth = Exponent`, `Gain / Velocity = UseSmoothing` | Identical (verified numerically), except exactly at `v == Midpoint` without gain whenever `smooth * input < 1` (not only at `smooth` 0: the step is hard in that whole range), where the fork still returns 1 and RawAccel already the value after the jump (corrected) (source: driver/accel_modes.c:514, driver/accel_modes.c:522; common/accel-jump.hpp:58, common/accel-jump.hpp:77 (v1.7.1)) |
| 5 Synchronous | `synchronous` (1.7) | `motivity = Motivity`, `gamma = Exponent`, `syncSpeed = Acceleration`, `smooth = Midpoint`, `Gain / Velocity = UseSmoothing` | Identical formula for `smooth > 0.03125` (corrected: the formula only; the fork's fixed-point result is off by up to 280 % for `smooth` below 0.25, see 8.5); with gain on, the tables differ slightly in precision. **Not the same for `smooth` 0 to 0.03125**, see the kernel branch in 1.5 (inferred) |
| 5 Synchronous | `motivity` (1.6) | `Motivity = motivity`, `Acceleration = midpoint`, `Exponent = ln(motivity) * exp(growthRate) / 2`, `Midpoint = 0.5` | Identical after this conversion (inferred, algebra). The reverse works only when the fork's `Midpoint` is 0.5 |
| 4 Motivity | none of that name fits | - | **Only the name is shared.** The fork's formula is a logistic over speed (source: driver/accel_modes.c:472-473); RawAccel 1.6 `motivity` is a sigmoid in log-log space (source: common/accel-motivity.hpp:22-26 (v1.6.1)) |
| 4 Motivity | `jump`, `Gain / Velocity` false | `cap.x = Midpoint`, `cap.y = Acceleration`, `smooth = 2 pi / Midpoint` | Identical, but only for `Midpoint >= 2 pi`, since `smooth` may not exceed 1 (inferred, algebra; numeric check agreed exactly) |
| 8 LUT | `lut`, `Gain / Velocity` false | same points | Identical rule: linear interpolation of the multiplier, first value below the first point, last segment continued (inferred). Limits differ: 128 points and 4096 characters in the fork, 257 points in RawAccel (source: driver/accel_modes.h:14-15; common/rawaccel-base.hpp:19-20 (v1.7.1)) |
| 8 LUT | `lut`, `Gain / Velocity` true | `y_fork = y / x` per point | Only similar: RawAccel interpolates the output speed, the fork the multiplier, so values between points differ (inferred). The fork's README also says velocity LUTs are not supported (source: README.org:232) |
| 9 Custom Curve | none | - | Fork only (GUI feature) |

Not representable on the other side:

- RawAccel only: by component mode, Lp norm other than 2, domain and range
  weights, cap modes `input` and `in_out`, legacy caps (`Gain / Velocity` false
  with a cap; corrected: a legacy output cap of at least 1 for classic, or of
  any value for power, equals the fork's `OutputCap = cap.y * Sensitivity`
  while `RatioYX` is 1 (verified numerically); it stays outside what QuadSens
  edits), a non-zero `inputOffset` in classic, `Input Speed Cap`, the
  smoothing halflives, snapping to both axes, the constant time option
  (inferred from 3.1 and 2.6).
- Fork only: `Offset` as a pure shift, negative `Offset` (the GUI allows -50,
  source: gui/main.cpp:181), `InputCap`, `OutputCap`, snapping to a free angle,
  Custom Curve, and classic or linear with `UseSmoothing` 1 and `Midpoint` 0
  (the fork then caps downward toward 0, while RawAccel treats `cap.y` 0 as "no
  cap") (source: driver/accel_modes.c:58-67; common/accel-classic.hpp:116
  (v1.7.1)).

A trap in the names: the fork's `UseSmoothing` is RawAccel's gain switch for
natural, jump and synchronous, and "gain cap on" for linear, classic and power;
it has nothing to do with RawAccel's `smooth` parameter, which is the fork's
`Exponent` in jump and `Midpoint` in synchronous (inferred from the table
above).

---

## 4. Recommended common subset for editing

Recommendation (inferred from sections 1 to 3; a proposal, not a finding).

**(corrected) The ranges in the table below are not safe limits for the
fork.** They were taken from RawAccel's validation and the fork's sliders, and
the fork's fixed-point code can divide by zero or overflow inside them (see
8.1 to 8.3). The limits QuadSens uses are in section 7. Synchronous is not
edited at all; its `smooth` range of 0.05 to 1 given here is also wrong for
the fork, which is usable only from about 0.25 (8.5).

### 4.1 Modes and parameters

One neutral model in QuadSens, each field mapped to both drivers. "Capped"
means a gain cap by output.

| Model | Fields (range; default) | Fork | RawAccel 1.7 / 1.6 |
|---|---|---|---|
| Off | - | `AccelerationMode` 0 | `mode` `noaccel` |
| Linear | acceleration (> 0, suggest 0.0001 to 1; 0.005); cap (off, or > 0 and not 1, suggest 0.1 to 10; off) | mode 1; `Acceleration`; cap off: `UseSmoothing` 0; cap on: `UseSmoothing` 1, `Midpoint` = cap | `classic`, `exponentClassic` 2, `acceleration`; cap off: `cap.y` 0; cap on: `Gain / Velocity` true, `cap.y` = cap; `Cap mode` `output` |
| Classic | acceleration (> 0, suggest 0.0001 to 2; 0.005); exponent (> 1, suggest 1.01 to 5; 2); cap as Linear | mode 3; `Acceleration`, `Exponent`, cap as Linear | `classic`, `acceleration`, `exponentClassic`, cap as Linear |
| Power | scale (> 0, suggest 0.001 to 5; 1); exponent (> 0, suggest 0.01 to 1; 0.05); output offset (>= 0; 0); cap (off, or > output offset; off) | mode 2; `Acceleration`, `Exponent`, `Midpoint`; cap off: `UseSmoothing` 0; cap on: `UseSmoothing` 1, `Motivity` = cap | `power`, `scale`, `exponentPower`, `outputOffset`; cap as Linear |
| Natural | decay rate (> 0, suggest 0.001 to 5; 0.1); limit (> 0 and not 1, suggest 0.01 to 8; 1.5); offset (>= 0; 0); gain (bool; true) | mode 6; `Acceleration`, `Exponent`, `Midpoint`, `UseSmoothing` | `natural`, `decayRate`, `limit`, `inputOffset`, `Gain / Velocity` |
| Jump | input (> 0, suggest 0.1 to 50; 15); output (> 0; 1.5); smooth (0 to 1; 0.5); gain (bool; true) | mode 7; `Midpoint`, `Acceleration`, `Exponent`, `UseSmoothing` | `jump`, `cap.x`, `cap.y`, `smooth`, `Gain / Velocity` |
| Synchronous (second step, 1.7 only) | motivity (> 1, suggest to 10; 1.5); gamma (> 0; 1); sync speed (> 0; 5); smooth (0.05 to 1; 0.5); gain (bool; true) | mode 5; `Motivity`, `Exponent`, `Acceleration`, `Midpoint`, `UseSmoothing` | `synchronous`, `motivity`, `gamma`, `syncSpeed`, `smooth`, `Gain / Velocity` |

- The defaults are RawAccel's (source: common/rawaccel-base.hpp:44-66
  (v1.7.1)); the fork's defaults are generic per parameter, not per mode
  (source: driver/defaults.h:13-17), so they are not useful as mode defaults
  (inferred).
- The hard limits are the intersection of RawAccel's validation (source:
  common/rawaccel-validate.hpp:53-129 (v1.7.1)) and the fork's rejections
  (source: driver/accel_modes.c:24-174). The "suggest" ranges follow the fork's
  sliders (source: gui/main.cpp:221-360).
- Synchronous is listed as a second step because of the difference at small
  `smooth` and because a 1.6 profile needs the conversion of 2.8 (inferred).
- Shared, outside the curve: sensitivity (`Sensitivity` / `Output DPI / 1000` /
  `Sensitivity multiplier`) and Y/X ratio (`RatioYX`). QuadSens already writes
  the RawAccel side of these for the four multipliers (source:
  quadsens/rawaccel.py:34-37), so editing them needs care not to fight that
  logic (inferred).

### 4.2 What must stay read-only

Show, but do not edit, and refuse to edit the curve while any of these is not
neutral (inferred):

- Fork: `Offset`, `InputCap`, `OutputCap`, `PreScale`, `RotationAngle`,
  `AngleSnap_Threshold`, `AngleSnap_Angle`, `LutSize`, `LutDataBuf`,
  `_CustomCurveDataAggregate`; modes 4, 8, 9.
- RawAccel: `Vertical accel parameters` and the whole flag (edit only profiles
  in whole mode), `lpNorm`, domain and range weights, `Cap mode` other than
  `output`, `Gain / Velocity` false together with a cap, `inputOffset` in
  classic, `Input Speed Cap`, the three halflife keys, rotation, snapping,
  `lut` and `data`, everything in `defaultDeviceConfig` and `devices`.

### 4.3 Biggest pitfalls

1. **The fork's default is not "off".** A fresh module runs Linear with 0.1
   (source: driver/defaults.h:2, driver/defaults.h:13). "No acceleration" on
   Linux means mode 0 (inferred).
2. **Rejections are silent.** The fork falls back to mode 0 internally and
   only logs to the kernel log; sysfs keeps showing the requested mode (source:
   driver/accel_modes.c:25-27). QuadSens has to validate before writing
   (inferred).
3. **Parameter names are reused.** `Midpoint` is the cap in Linear and
   Classic, the output offset in Power, the offset in Natural, the jump speed
   in Jump and the smoothness in Synchronous (source: 1.5). Switching the mode
   without rewriting all four shared parameters gives a different curve
   (inferred).
4. **RawAccel validates unused fields.** Keep every key of the arguments
   object and keep unused values valid (source:
   common/rawaccel-validate.hpp:91-129 (v1.7.1)).
5. **Decimal text only** for the fork, no exponent notation (source:
   driver/FixedMath/Fixed64.h:1526).
6. **Write order on the fork:** all parameters first, `update` last; the
   change needs a mouse movement and can take up to a second (source:
   driver/accel.c:108-113). `UseSmoothing` is live at once (source:
   driver/accel.c:72), so write it directly before `update` (inferred).
7. **Angles** are radians in sysfs and degrees in `/etc/yeetmouse.conf`
   (source: gui/DriverHelper.cpp:417, gui/ConfigHelper.cpp:66).
8. **Persistence on the fork** is a separate step: sysfs is lost at reboot
   unless `/etc/yeetmouse.conf` is rewritten (source:
   install_files/systemd/yeetmouse.service:11). `accelMode` there must be the
   enum string (source: gui/ConfigHelper.h:99-108).
9. **Units only match** with RawAccel DPI 0 and fork `PreScale` 1, or with
   `PreScale = 1000 / DPI` (inferred, see 3.1).
10. **RawAccel 1.6 has no synchronous and different key names** (source: 2.8).
11. **(added) Six decimals are not enough.** QuadSens' format for the
    directional values has six decimals; for curve parameters that changes
    small values noticeably and turns anything below 5e-7 into 0. The editor
    writes ten decimals (rule R6 in section 7).
12. **(added) RawAccel's `Cap / Jump` slot is shared** between classic, power
    and jump and defaults to `{15, 1.5}` with cap mode `output`. A classic or
    power curve "without cap" therefore must be written with `y` 0, or it is
    capped at 1.5.
13. **(added) Natural's bounds "greater than 0" are too weak for the fork:**
    a tiny decay rate makes an internal quotient round to 0 and the next
    division fails (8.1).
14. **(added) A rejection also sets the fork's internal `Acceleration` to 0**,
    not only the mode (source: driver/accel_modes.c:26, and the same pattern
    in the other rejections).

---

## 5. Interaction with directional scaling

- The fork applies the four multipliers to the raw deltas as the very first
  step, before `PreScale`, before the speed is computed and before the curve;
  which multiplier is used depends on the sign of the raw input (source:
  driver/accel.c:223-229, DIRECTIONAL_SCALING.md:14-17).
- RawAccel applies its numbers as the very last step, after the curve; L/R and
  U/D depend on the sign of the output (source: common/rawaccel.hpp:412-422
  (v1.7.1), common/rawaccel.hpp:186-196 (v1.6.1)).
- QuadSens maps right, left, down, up (R, L, D, U) to
  `s = s0 R`, `yx = yx0 D / R`, `lr = lr0 L / R`, `ud = ud0 U / D` (source:
  quadsens/rawaccel.py:34-37).

For movement along one direction with multiplier `k`, the same curve `f` on
both sides, and everything else neutral (inferred from 1.4 and 2.5):

    fork:      out = d * k * f(k * v)
    RawAccel:  out = d * k * f(v)

For diagonal movement the fork's curve sees
`sqrt((kx dx)^2 + (ky dy)^2) / ms`, RawAccel's sees `sqrt(dx^2 + dy^2) / ms`
(inferred).

Consequences (inferred):

- **Without a curve the result is the same.** With `f = 1` both sides give
  `d * k`. "Exact for profiles without acceleration" stays right. On the fork
  side "without acceleration" means mode 0, not the default mode.
- **With a curve the result differs**, because the curve is evaluated at a
  speed that is `k` times smaller or larger. "Approximation" stays right, also
  when both drivers run the identical curve. The relative difference is about
  `(1 - k) * v f'(v) / f(v)`. Example: linear with 0.05, 20 counts/ms,
  `k` = 0.97: the fork gives a factor 1.911, RawAccel 1.940, which is 1.5 %
  more (computed from the formulas, not measured).
- The difference is largest where the curve is steep. For jump with `smooth` 0
  it can be the full jump height, but only in the narrow band of speeds between
  `Midpoint` and `Midpoint / k`.
- Two further conditions for "exact" that QuadSens does not check today: its
  check looks only at the mode (source: quadsens/rawaccel.py:177-187).
  - Rotation: the fork picks the multiplier by the raw direction and rotates
    afterwards (source: driver/accel.c:224-229, driver/accel.c:252-256);
    RawAccel rotates first and picks by the output direction (source:
    common/rawaccel.hpp:322, common/rawaccel.hpp:416-422 (v1.7.1)). With a
    rotation set, the same four numbers act on different physical directions.
  - RawAccel's angle snapping changes the direction before the multipliers
    are picked (source: common/rawaccel.hpp:324-344 (v1.7.1)).
- Even in the exact case single reports can differ by one count because of the
  different rounding; the sums agree (inferred, see 3.1).
- A closer approximation with a curve would be possible when left equals right
  and up equals down: RawAccel's domain weights `x = kx`, `y = ky` then give
  its curve the same speed as the fork's. With four different numbers RawAccel
  has no setting that scales the curve input by direction (inferred from
  common/rawaccel.hpp:353-356 (v1.7.1)). This is an option to discuss, not a
  recommendation.

---

## 6. Open questions / not verified

- Nothing here was run: no module was loaded, no settings file was produced by
  a real RawAccel, no cursor movement was measured.
- The version string: `common/rawaccel-version.h` at the tag `v1.7.1` defines
  patch level 0 (source: common/rawaccel-version.h:3-5 (v1.7.1)), and the
  settings file takes its `version` from that header (source:
  wrapper/wrapper.cpp:659 (v1.7.1)). A file written by the 1.7.1 release may
  therefore say `"version": "1.7.0"`. The QuadSens fixture says `1.7.1`
  (source: tests/fixtures/rawaccel/settings-1.7.1-default.json:4). Needs a file
  from a real installation.
- Whether RawAccel accepts an arguments object with a missing key (the
  arguments struct is not marked "all required", unlike the profile; source:
  wrapper/wrapper.cpp:57-58, wrapper/wrapper.cpp:208 (v1.7.1)) was not
  determined. Safest is to keep all keys.
- How the RawAccel GUI (not the writer) reacts to a file changed by another
  program while it is open was not looked at.
- The Synchronous clamp branch in the fork's kernel code (1.5) looks like a
  bug; whether the upstream project knows it, and whether it shows on hardware,
  is not verified.
- The kernel side of sysfs writes (trailing newline handling, maximum length
  of a `charp` value) was not read; it is kernel code, not part of the fork.
- Behaviour of the fork with a time difference of 0 between two reports (no
  lower time limit, division by the time) was not analysed.
- The fixed-point `exp`, `log` and `pow` of the fork have limited range and
  precision; how far they deviate from RawAccel's doubles at extreme
  parameters was not quantified.
- Whether the fork's GUI preview matches the kernel code everywhere was only
  spot-checked (two differences found: the Synchronous clamp, and the order of
  `OutputCap` and `Sensitivity`, source: gui/FunctionHelper.cpp:339,
  driver/accel.c:297-302).
- RawAccel versions other than the two tags (for example 1.6.0 or 1.7.0) were
  not read.

---

## 7. Rules the editor enforces

What QuadSens edits and within which limits. The limits are the intersection
of what RawAccel's validation accepts, what the fork accepts, and what the
fork's fixed-point code computes without a divide error or overflow. They
were checked with a sweep of 5000 random parameter sets per mode against the
userspace build of the fork's code: no set that passes these rules trapped or
was rejected there (verified numerically). They are implemented in
`quadsens/accel_edit.py` and, independently, in `helper/quadsens-apply`.

### 7.1 Editable modes, parameters and ranges

The conversions are the identity on these fields in both directions. Combined
("whole") mode only. A RawAccel `classic` with `exponentClassic` 2 reads as
Linear.

| Mode | Fields and ranges (default) | Fork | RawAccel |
|---|---|---|---|
| Off | - | `AccelerationMode` 0 | `mode` `noaccel` |
| Linear | acceleration 0.0001 to 1 (0.005); cap off or 0.1 to 10 | mode 1, `Acceleration`; cap off: `UseSmoothing` 0; on: `UseSmoothing` 1, `Midpoint` = cap | `classic`, `acceleration`, `exponentClassic` 2, `inputOffset` 0, `Cap mode` `output`, `cap.y` 0 (off) or cap, `Gain / Velocity` true |
| Classic | acceleration 0.0001 to 2 (0.005); exponent 1.01 to 5 (2); cap as Linear | mode 3, `Acceleration`, `Exponent`, cap as Linear | as Linear with `exponentClassic` = exponent |
| Power | scale 0.001 to 5 (1); exponent 0.01 to 1 (0.05); output offset 0 to min(5, 99 scale exponent) (0); cap off or above the offset, up to 10 | mode 2, `Acceleration` = scale, `Exponent`, `Midpoint` = offset; cap off: `UseSmoothing` 0; on: `UseSmoothing` 1, `Motivity` = cap | `power`, `scale`, `exponentPower`, `outputOffset`, `Cap mode` `output`, `cap.y` 0 or cap, `Gain / Velocity` true |
| Natural | decay rate 0.001 to 5 (0.1); limit 0.01 to 8 with a distance of at least 0.01 from 1 (1.5); offset 0 to 50 (0); gain on or off | mode 6, `Acceleration` = decay rate, `Exponent` = limit, `Midpoint` = offset, `UseSmoothing` = gain | `natural`, `decayRate`, `limit`, `inputOffset` = offset, `Gain / Velocity` = gain |
| Jump | input 0.1 to 50 (15); output 0.01 to 10 (1.5); smooth 0 to 1 (0.5); gain on or off | mode 7, `Midpoint` = input, `Acceleration` = output, `Exponent` = smooth, `UseSmoothing` = gain | `jump`, `cap.x` = input, `cap.y` = output, `smooth`, `Gain / Velocity` = gain |

Preconditions for the two drivers to compute the same curve: fork `Offset` 0,
`InputCap` 0, `OutputCap` 0; RawAccel whole mode, `lpNorm` 2, domain and
range weights 1, `Input Speed Cap` 0, the three halflives 0, `Cap mode`
`output`, classic `inputOffset` 0; and equal speed units (RawAccel device DPI
0 and fork `PreScale` 1).

### 7.2 Rules before writing

- **R1** Every internal point of the curve lies at or below 100000 counts/ms,
  computed in logarithms so that the check itself cannot overflow. Linear and
  Classic with a cap: `cx = (|cap - 1| / e)^(1 / (e - 1)) / acceleration`
  (`e` = 2 for Linear). Power: `ox = (offset / (n + 1))^(1/n) / scale` and,
  with a cap, `cx = (cap / (n + 1))^(1/n) / scale`.
- **R2** Power: offset at most `99 * scale * exponent` (the fork rejects above
  100 and then switches acceleration off silently).
- **R3** Power with a cap: cap strictly above the offset.
- **R4** The ranges of 7.1.
- **R5** The multiplier of the reference curve at 1000 counts/ms is at most
  100000.
- **R6** Numbers are written to the fork in fixed notation with ten decimals,
  with a leading digit and a dot, never in exponent notation. The fork's
  parser fails on `5e-05` and on `.5` and then keeps the old value; values
  below 2.33e-10 become 0.
- **R7** Write order on the fork: all string parameters (`Acceleration`,
  `Exponent`, `Midpoint`, `Motivity`, `AccelerationMode`), then
  `UseSmoothing`, then `update` = 1; all four slots and `UseSmoothing` are
  rewritten on every change. `UseSmoothing` is live at once while the
  constants wait for `update`; for Power, `UseSmoothing` 1 with stale cap
  constants gives a multiplier of 0 (the pointer stands still) until `update`
  has run.
- RawAccel: every write sets `Cap / Jump`.y, `Cap mode`, `Gain / Velocity`
  and, for classic, `inputOffset`, keeps every key of the arguments object
  including `data`, and passes a port of RawAccel's own check
  (`common/rawaccel-validate.hpp`) before `writer.exe` is run.

### 7.3 Shown read-only, never converted

- Fork: `Offset`, `InputCap` or `OutputCap` not 0; `PreScale` not 1; modes 4,
  5, 8 and 9; Linear or Classic with `UseSmoothing` 1 and `Midpoint` 0;
  Classic with `Exponent` at most 1; Jump with `Exponent` above 1 or
  `Acceleration` not above 0; a negative `Midpoint`; anything that violates
  R1 to R5.
- RawAccel: by component mode; `lpNorm` not 2; domain or range weights not 1;
  `Input Speed Cap` not 0; any halflife not 0; modes `lut`, `synchronous`,
  `motivity`; classic or power with `Cap mode` other than `output`; classic
  with `inputOffset` not 0; classic or power with `Gain / Velocity` false and
  `cap.y` above 0; power with `cap.y` equal to `outputOffset`; natural with
  `limit` 1; a device DPI other than 0.
- Never touched by the editor: rotation, snapping, sensitivity and ratios,
  the four directional numbers, anything under `devices` and
  `defaultDeviceConfig`.

On Linux the write is also refused when the module is currently in one of the
read-only configurations above: nothing is written over a configuration
QuadSens does not edit.

### 7.4 What the driver tells, and what it does not

The fork has no parameter that shows its internal mode. When it rejects a
set, it sets the internal mode to 0 and logs a line with `printk`; the sysfs
parameters keep the strings that were written (source:
driver/accel_modes.c:24-28 and the other rejections; driver/accel.c:58, the
mode is a string parameter). The kernel log is not readable for normal users.
After a write QuadSens therefore reads the strings back and compares them
with what it wrote, which shows that the write arrived, and says that the
driver does not report whether it accepted the values. Acceptance rests on
the rules above.

---

## 8. What the verification found in the fork

All of the following was observed in a userspace build of the unmodified
sources or read in them. What a running kernel does was not tested.

### 8.1 Divide errors

`FP64_DivPrecise` uses a bare 128/64-bit division (source:
driver/FixedMath/Fixed64.h:281-387) and `update_constants()` reaches it
without range checks. In the userspace build the process stopped with SIGFPE
for these sets; what the kernel does on the corresponding divide error is
unknown.

- Power with a cap when `(cap / (E + 1))^(1/E) / A` reaches 2^31, for example
  scale 0.5, exponent 0.05, cap 3, which is inside the sliders of the fork's
  own settings program (source: driver/accel_modes.c:199-203).
- Power with an output offset when `(M / (E + 1))^(1/E) / A` reaches 2^31, for
  example scale 0.9, exponent 0.05, offset 4 (source:
  driver/accel_modes.c:185-189).
- Classic with a cap when `(|cap - 1| / E)^(1 / (E - 1)) / A` reaches 2^31,
  for example acceleration 0.001, exponent 1.01, cap 5 (source:
  driver/accel_modes.c:93-94).
- Linear with a cap when `(|cap - 1| / 2) / A` reaches 2^31, for example
  acceleration 2e-9, cap 10 (source: driver/accel_modes.c:66).
- Natural when `A / |E - 1|` rounds to 0, for example decay rate 1e-9, limit 8
  (source: driver/accel_modes.c:121-122).

### 8.2 Silent switch-off

A rejected set makes the internal mode 0 and the internal `Acceleration` 0,
with a `printk` only; the sysfs strings stay (source:
driver/accel_modes.c:24-28, 51-55, 78-82, 110-119, 128-133, 156-174).
Rejected are: Linear with `A` 0; Classic with `UseSmoothing` 1 and `E` 0 or
1; Power with `E` 0 or -1, with `A` 0, with `UseSmoothing` 1 and `M >= V`, or
with `M / (A E)` above 100; Natural with `E` 1 or `A` 0; Jump with `M` 0;
Synchronous with `V` at most 1. RawAccel accepts two of these that the fork
rejects: a natural limit of 1 and a power cap equal to the output offset.

### 8.3 Overflow without a trap

`FP64_Exp2` saturates at 2^31, and adding 1 wraps to a negative number, for
example Classic with acceleration 1.6 and exponent 4.95 without a cap at 150
counts/ms (source: driver/FixedMath/Fixed64.h:824-838).

### 8.4 Time difference

The time between two reports, in nanoseconds, passes through a 32-bit
`FP64_FromInt` before the 100 ms limit is applied (source:
driver/FixedMath/Fixed64.h:95-97, driver/accel.c:199, driver/accel.c:209).
Pauses between 2.147 s and 4.295 s give a negative time; `k * 4.295 s + t`
gives `t`; exact multiples divide by zero. There is one static time stamp for
all devices and no lower limit.

### 8.5 Other observations

- For a speed at or below 0 the curve value is the constant `f(0.001)`; the
  constant is named `FP64_0_01` but holds 0.001 (source:
  driver/accel.c:260-292, driver/accel_modes.h:70).
- The fork's default is Linear with 0.1, `UseSmoothing` 1 and `Midpoint` 1.3,
  that is a capped linear curve, not "off" (source: driver/defaults.h).
- Kernel and settings program disagree on the order of `OutputCap` and
  `Sensitivity`: the kernel computes `min(OutputCap, f * Sensitivity)`, the
  preview of the settings program `min(f, OutputCap) * Sensitivity` (source:
  driver/accel.c:297-302, gui/FunctionHelper.cpp:339).
- Synchronous: the kernel's clamp branch for `smooth` at most 0.03125 is a
  step, the settings program's is the intended clamp (source:
  driver/accel_modes.c:288-293, gui/FunctionHelper.cpp:33-38); below `smooth`
  0.25 the fixed-point result is off by up to 280 %.
- The fork's own tools write numbers with the C++ stream default: six
  significant digits of a single-precision float, and exponent notation below
  0.0001 (source: gui/DriverHelper.cpp:66-74, gui/ConfigHelper.cpp:54-72).
  `yeetmousectl save` followed by a load therefore keeps six significant
  digits of what QuadSens wrote with ten decimals, and a value below 0.0001
  would be written back as, for example, `5e-05`, which the module's parser
  does not read. The ranges of 7.1 start at 0.0001, where the stream still
  uses fixed notation (inferred from the C++ formatting rules; not run).

### 8.6 Accuracy of the fork's fixed point

Largest relative difference of the multiplier between the fork's fixed-point
code and RawAccel's formulas in double precision, for speeds from 0.01 to 150
counts/ms, inside the limits of section 7 (verified numerically): Linear
5.7e-8, Classic 1.4e-5, Power 2.1e-5, Jump 1.6e-5, Natural without gain 1e-7,
Natural with gain up to 1e-2 at 0.01 counts/ms and falling roughly with 1/v
(7e-4 at 0.125, 3e-5 at 1).

### 8.7 Still uncertain

- What the kernel does on the divide error, and whether the installed module
  takes the same 128-bit division path as the userspace build.
- How Json.NET in RawAccel treats a missing key in the arguments object.
- Details of sysfs writes (trailing newline, length limits).
- RawAccel 1.6.0 and 1.7.0 were not read.
