#!/usr/bin/env python3
"""Render the two icon drawings into assets/quadsens.ico for the Windows executable.

assets/quadsens-small.svg gives the 16 and 24 pixel images, assets/quadsens.svg
the larger ones.

Usage: python3 tools/make_icon.py

The .ico holds the icon at 16, 24, 32, 48, 64, 128 and 256 pixels, each stored
as PNG (which Windows reads since Vista). The file is committed, so building
on Windows does not need this step; run it again after changing an SVG.
"""

from __future__ import annotations

import os
import struct
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from quadsens.ui.qt import QtCore, QtGui, QtWidgets  # noqa: E402

SIZES = (16, 24, 32, 48, 64, 128, 256)


def png_bytes(icon: QtGui.QIcon, size: int) -> bytes:
    image = QtGui.QImage(size, size, QtGui.QImage.Format.Format_ARGB32)
    image.fill(QtCore.Qt.GlobalColor.transparent)
    painter = QtGui.QPainter(image)
    icon.paint(painter, QtCore.QRect(0, 0, size, size))
    painter.end()
    buffer = QtCore.QBuffer()
    buffer.open(QtCore.QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    return bytes(buffer.data().data())


def main() -> int:
    app = QtWidgets.QApplication([sys.argv[0]])   # noqa: F841 (needed for rendering)
    icon = QtGui.QIcon(str(ROOT / "assets" / "quadsens.svg"))
    small = QtGui.QIcon(str(ROOT / "assets" / "quadsens-small.svg"))
    if icon.pixmap(32, 32).isNull() or small.pixmap(16, 16).isNull():
        print("the SVG could not be rendered (is Qt's SVG plugin installed?)", file=sys.stderr)
        return 1
    images = [png_bytes(small if size <= 24 else icon, size) for size in SIZES]
    header = struct.pack("<HHH", 0, 1, len(SIZES))
    offset = len(header) + 16 * len(SIZES)
    entries = b""
    for size, data in zip(SIZES, images):
        side = 0 if size == 256 else size          # 0 stands for 256 in an .ico
        entries += struct.pack("<BBBBHHII", side, side, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
    target = ROOT / "assets" / "quadsens.ico"
    target.write_bytes(header + entries + b"".join(images))
    print("wrote %s (%d bytes, %d sizes)" % (target, target.stat().st_size, len(SIZES)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
