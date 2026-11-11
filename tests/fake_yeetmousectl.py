#!/usr/bin/env python3
"""Stand-in for yeetmousectl in tests and for the screenshot tool.

    fake_yeetmousectl.py save FILE

Dumps the values of a fake parameter directory to FILE the way the real tool
does. Controlled by two environment variables:

    QUADSENS_FAKE_PARAMS  directory with the four DirScale* files
    QUADSENS_FAKE_MODE    "fork" (default) writes the four dirScale keys,
                          "stock" leaves them out like the unmodified tool,
                          "fail" exits with status 3
"""

import os
import sys

KEYS = (("dirScaleXPos", "DirScaleXPos"), ("dirScaleXNeg", "DirScaleXNeg"),
        ("dirScaleYPos", "DirScaleYPos"), ("dirScaleYNeg", "DirScaleYNeg"))


def main(argv: list[str]) -> int:
    if len(argv) != 3 or argv[1] != "save":
        print("Unknown command", file=sys.stderr)
        return 1
    mode = os.environ.get("QUADSENS_FAKE_MODE", "fork")
    if mode == "fail":
        print("cannot open config", file=sys.stderr)
        return 3
    lines = ["sens=1", "accelMode=1"]
    if mode != "stock":
        params = os.environ["QUADSENS_FAKE_PARAMS"]
        for key, name in KEYS:
            with open(os.path.join(params, name), encoding="ascii") as handle:
                lines.append("%s=%g" % (key, float(handle.read())))
    lines.append("LUT_size=0")
    with open(argv[2], "w", encoding="ascii") as handle:
        handle.write("\n".join(lines) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
