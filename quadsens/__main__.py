"""Entry point: ``quadsens`` or ``python -m quadsens``."""

from __future__ import annotations

import os
import signal
import sys

from . import __version__


def default_state_path() -> str:
    base = os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")
    folder = os.path.join(base, "quadsens")
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, "virtual-mouse-state.json")


def main(argv: list[str] | None = None) -> int:
    try:
        from .ui.qt import QtCore, QtWidgets
    except ImportError:
        print("quadsens: PySide6 is not installed. On Arch/Manjaro: sudo pacman -S pyside6",
              file=sys.stderr)
        return 1
    from . import platforms
    from .ui.icon import app_icon

    app = QtWidgets.QApplication(sys.argv if argv is None else [sys.argv[0], *argv])
    app.setOrganizationName("QuadSens")
    app.setApplicationName("QuadSens")
    app.setDesktopFileName("quadsens")
    app.setApplicationVersion(__version__)
    app.setWindowIcon(app_icon())

    window: QtWidgets.QMainWindow
    if platforms.current() == platforms.WINDOWS:
        # Windows applies values only. Nothing of the Linux chain (virtual mouse,
        # yeetmouse, pkexec) is imported on this path.
        from .ui.windows_window import WindowsMainWindow
        window = WindowsMainWindow(QtCore.QSettings(), platforms.default_result_glob())
    else:
        from .calibration import DEFAULT_GLOB
        from .driver import Driver
        from .ui.main_window import MainWindow
        window = MainWindow(QtCore.QSettings(), DEFAULT_GLOB, Driver(), default_state_path())
    app.aboutToQuit.connect(window.shutdown)

    # Let Ctrl+C / SIGTERM close the window so the virtual device is torn down.
    signal.signal(signal.SIGINT, lambda *_: app.quit())
    signal.signal(signal.SIGTERM, lambda *_: app.quit())
    wakeup = QtCore.QTimer()
    wakeup.timeout.connect(lambda: None)  # gives Python a chance to run signal handlers
    wakeup.start(250)

    window.show()
    QtCore.QTimer.singleShot(0, window.apply_saved_if_enabled)
    return int(app.exec())


if __name__ == "__main__":
    sys.exit(main())
