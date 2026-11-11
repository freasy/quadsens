# Acceleration test vectors

`vectors.json` was produced by an independent verification of both drivers'
sources (the yeetmouse fork 0.9.5+dirscale1 and RawAccel v1.7.1): reference
implementations written from each driver's source, and the fork's own
unmodified fixed-point C code run in a userspace build. No kernel module was
loaded and no real RawAccel was run. Its own `_about` and `_editor_rules`
entries say what the columns mean. The file is used unchanged.
