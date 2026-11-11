"""Import and export of the values file, shared by the Linux and the Windows window."""

from __future__ import annotations

from pathlib import Path

from .. import values_file
from .qt import QtGui, QtWidgets


class ValuesIO:
    """Mixin for a main window that has a ``calibration_panel``.

    Adds "Import values…" and "Export values…": the first reads a QuadSens
    values file and makes its multipliers the result to work with, the second
    writes the current result to such a file.
    """

    calibration_panel: object

    def add_values_actions(self, menu: QtWidgets.QMenu) -> None:
        self.import_action = QtGui.QAction("&Import values…", self)   # type: ignore[arg-type]
        self.import_action.setShortcut(QtGui.QKeySequence("Ctrl+I"))
        self.import_action.setToolTip("Read the four multipliers from a QuadSens values file")
        self.import_action.triggered.connect(lambda: self.import_values())
        self.export_action = QtGui.QAction("&Export values…", self)   # type: ignore[arg-type]
        self.export_action.setShortcut(QtGui.QKeySequence("Ctrl+E"))
        self.export_action.setToolTip(
            "Write the four multipliers to a file, to use them on another machine")
        self.export_action.triggered.connect(lambda: self.export_values())
        menu.addAction(self.import_action)
        menu.addAction(self.export_action)

    def import_values(self, path: str = "") -> bool:
        """Import from ``path``; without one, ask for the file. True on success."""
        panel = self.calibration_panel
        if not path:
            path, _filter = QtWidgets.QFileDialog.getOpenFileName(
                self, "Import values", "", values_file.FILE_FILTER)   # type: ignore[arg-type]
            if not path:
                return False
        try:
            with open(path, encoding="utf-8-sig") as handle:
                text = handle.read(values_file.MAX_SIZE + 1)
            panel.import_values(text)                       # type: ignore[attr-defined]
        except OSError as exc:
            panel.message.show_message(                      # type: ignore[attr-defined]
                f"Not imported: {Path(path).name} cannot be read ({exc.strerror or exc}).",
                error=True)
            return False
        except values_file.ValuesError as exc:
            panel.message.show_message(                      # type: ignore[attr-defined]
                f"Not imported: {exc}.", error=True)
            return False
        self.values_imported()
        return True

    def export_values(self, path: str = "") -> bool:
        """Export to ``path``; without one, ask where to. True on success."""
        panel = self.calibration_panel
        try:
            text = panel.export_text()                      # type: ignore[attr-defined]
        except values_file.ValuesError as exc:
            panel.message.show_message(                      # type: ignore[attr-defined]
                f"Not exported: {exc}.", error=True)
            return False
        if not path:
            path, _filter = QtWidgets.QFileDialog.getSaveFileName(
                self, "Export values", "multipliers" + values_file.EXTENSION,   # type: ignore
                values_file.FILE_FILTER)
            if not path:
                return False
        try:
            with open(path, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(text)
        except OSError as exc:
            panel.message.show_message(                      # type: ignore[attr-defined]
                f"Not exported: the file cannot be written ({exc.strerror or exc}).",
                error=True)
            return False
        panel.message.show_message(                          # type: ignore[attr-defined]
            f"✓ Exported the four multipliers to {Path(path).name}.")
        return True

    def values_imported(self) -> None:
        """Hook for the window: values were imported."""
