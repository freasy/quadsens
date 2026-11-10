"""PyInstaller entry point for the QuadSens binary."""

import sys

from quadsens.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
