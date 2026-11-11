# RawAccel settings fixtures

These files are test input for `quadsens/rawaccel.py`. They are not copies of a
file from the RawAccel repository (it ships none); they were written to match
what RawAccel itself writes, from the JSON property names and default values in
its sources:

- `wrapper/wrapper.cpp` and `common/rawaccel-base.hpp` at the tag `v1.7.1`
- the same files at the tag `v1.6.1`

of <https://github.com/RawAccelOfficial/rawaccel>, which is published under the
MIT license (Copyright (c) 2020 a1xd). The files use two-space indentation and
CRLF line endings, like the .NET JSON writer RawAccel uses on Windows.

- `settings-1.7.1-default.json`: one profile with the defaults of 1.7.1
- `settings-1.7.1-profiles.json`: three profiles, two of them with an
  acceleration curve and one with its own values for the four settings
- `settings-1.6.1-default.json`: one profile with the 1.6 setting names

No file here was produced by a real RawAccel installation.
