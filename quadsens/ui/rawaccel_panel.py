""""RawAccel" card of the Windows window: preview and apply the four settings."""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from pathlib import Path

from .. import rawaccel
from ..calibration import Result, driver_scalars
from ..model import SCALE_MAX, SCALE_MIN, Scalars
from ..rawaccel import Four, RawAccelError
from .qt import QtCore, QtWidgets, Signal
from .scale_spin import ScaleSpinBox
from .status import (
    SPACE_M,
    SPACE_S,
    Card,
    ElideLabel,
    Disclosure,
    MessageLabel,
    WrapLabel,
    caption,
    column_header,
    heading,
)

ROW_TITLES: tuple[str, ...] = ("→ &Right:", "← &Left:", "↓ &Down:", "↑ &Up:")
DIRECTIONS: tuple[str, ...] = ("right", "left", "down", "up")
DECIMALS = 4
NO_VALUE = "–"
BOOT_HINT = ("RawAccel forgets its settings when Windows shuts down. The values stay in "
             "settings.json; whatever loads that file at start brings them back.")
FLIP_HINT = ("The up/down mapping was verified on Linux only. Switch this on if up and "
             "down feel swapped after applying.")


class RawAccelPanel(Card):
    """Shows what would change in RawAccel's settings and writes it on request.

    "New" is always computed from the baseline, the values the profile had
    before QuadSens touched it, so applying again never multiplies twice. The
    baseline is remembered in the application settings, and the first write to
    a settings file leaves a timestamped copy of the original next to it.
    """

    changed = Signal()

    def __init__(self, settings: QtCore.QSettings,
                 get_result: Callable[[], Result | None],
                 environ: Mapping[str, str] | None = None,
                 parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__("RawAccel", parent)
        self._settings = settings
        self._get_result = get_result
        self._environ = os.environ if environ is None else environ
        self._process: QtCore.QProcess | None = None
        self._timer = QtCore.QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(int(rawaccel.WRITER_TIMEOUT * 1000))
        self._timer.timeout.connect(self._writer_slow)
        self.folder: Path | None = None
        self.document: rawaccel.Document | None = None
        self.problem = ""          # why the settings are not usable, "" if they are
        self.baseline_note = ""

        self.folder_edit = QtWidgets.QLineEdit()
        self.folder_edit.setReadOnly(True)
        self.folder_edit.setPlaceholderText("no folder chosen yet")
        self.folder_edit.setAccessibleName("RawAccel folder")
        self.folder_edit.setAccessibleDescription(
            "The folder that holds rawaccel.exe, writer.exe and settings.json.")
        # No mnemonics here: every letter of both texts belongs to another control.
        folder_label = QtWidgets.QLabel("Folder:")
        self.choose_button = QtWidgets.QPushButton("Choose folder…")
        self.choose_button.setToolTip("Choose the folder where you unpacked RawAccel")
        self.choose_button.clicked.connect(lambda: self.choose_folder())
        folder_row = QtWidgets.QHBoxLayout()
        folder_row.setSpacing(SPACE_M)
        folder_row.addWidget(folder_label)
        folder_row.addWidget(self.folder_edit, 1)
        folder_row.addWidget(self.choose_button)

        self.profile_combo = QtWidgets.QComboBox()
        self.profile_combo.setAccessibleName("RawAccel profile")
        self.profile_combo.setToolTip("The profile whose four settings are changed")
        self.profile_combo.setAccessibleDescription(self.profile_combo.toolTip())
        self.profile_combo.currentIndexChanged.connect(lambda *_: self._refresh())
        profile_label = QtWidgets.QLabel("&Profile:")
        profile_label.setBuddy(self.profile_combo)
        # Names and versions come from the file: they may be long, and must not
        # widen the window.
        self.profile_combo.setSizeAdjustPolicy(
            QtWidgets.QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.profile_combo.setMinimumContentsLength(8)
        self.version_label = caption(ElideLabel())
        self.version_label.setAccessibleName("RawAccel settings version")
        profile_row = QtWidgets.QHBoxLayout()
        profile_row.setSpacing(SPACE_M)
        profile_row.addWidget(profile_label)
        profile_row.addWidget(self.profile_combo)
        profile_row.addWidget(self.version_label, 1)

        # Left: the multipliers to apply. Right: what that means for the four settings.
        editor = QtWidgets.QGridLayout()
        editor.setHorizontalSpacing(SPACE_M)
        editor.setVerticalSpacing(2)
        editor.addWidget(column_header("Direction"), 0, 0)
        editor.addWidget(column_header("Multiplier"), 0, 1)
        self.spins: list[ScaleSpinBox] = []
        for row, (title, direction) in enumerate(zip(ROW_TITLES, DIRECTIONS), start=1):
            spin = ScaleSpinBox()
            spin.setRange(SCALE_MIN, SCALE_MAX)
            spin.setDecimals(DECIMALS)
            spin.setSingleStep(0.001)
            spin.setValue(1.0)
            spin.setAccessibleName(f"Multiplier for movement {direction}")
            spin.setAccessibleDescription(
                f"How much faster or slower the mouse moves {direction}. 1 leaves it "
                "unchanged. Dot or comma as decimal separator.")
            spin.setToolTip(f"Multiplier for mouse movement {direction} (1 = unchanged)")
            spin.valueChanged.connect(lambda *_: self._refresh())
            label = QtWidgets.QLabel(title)
            label.setBuddy(spin)
            editor.addWidget(label, row, 0)
            editor.addWidget(spin, row, 1)
            self.spins.append(spin)

        preview = QtWidgets.QGridLayout()
        preview.setHorizontalSpacing(SPACE_M)
        preview.setVerticalSpacing(2)
        for column, title in enumerate(("RawAccel setting", "Current", "New")):
            preview.addWidget(column_header(title), 0, column)
        self.setting_labels: list[QtWidgets.QLabel] = []
        self.current_labels: list[QtWidgets.QLabel] = []
        self.new_labels: list[QtWidgets.QLabel] = []
        for row in range(1, 5):
            name, current, new = QtWidgets.QLabel(NO_VALUE), QtWidgets.QLabel(NO_VALUE), \
                QtWidgets.QLabel(NO_VALUE)
            for label in (current, new):
                label.setTextInteractionFlags(
                    QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
                label.setMinimumHeight(28)   # lines up with the spin boxes beside it
            preview.addWidget(name, row, 0)
            preview.addWidget(current, row, 1)
            preview.addWidget(new, row, 2)
            self.setting_labels.append(name)
            self.current_labels.append(current)
            self.new_labels.append(new)
        preview.setColumnStretch(0, 1)
        tables = QtWidgets.QHBoxLayout()
        tables.setSpacing(SPACE_M * 2)
        tables.addLayout(editor)
        tables.addLayout(preview, 1)

        self.state_label = ElideLabel()
        self.state_label.setAccessibleName("Whether the settings file holds the new values")
        self.note_label = caption(WrapLabel("How exact the result is"))
        self.boot_label = caption(WrapLabel("About restarting Windows"))
        self.boot_label.setText(BOOT_HINT)

        self.use_button = QtWidgets.QPushButton("Use resul&t")
        self.use_button.setToolTip("Fill the multipliers from the calibration result or the "
                                   "imported values")
        self.use_button.clicked.connect(self.use_result)
        self.reset_button = QtWidgets.QPushButton("Reset to 1.&0")
        self.reset_button.setToolTip("Set all four multipliers to 1.0 (no change)")
        self.reset_button.clicked.connect(lambda: self.set_values(Scalars.unity()))
        self.apply_button = QtWidgets.QPushButton("&Apply")
        self.apply_button.setToolTip(
            "Write the new values into settings.json and let RawAccel load them")
        self.apply_button.clicked.connect(self.apply)
        self.restore_button = QtWidgets.QPushButton("Restore ori&ginal")
        self.restore_button.setAccessibleName("Restore original RawAccel values")
        self.restore_button.setToolTip(
            "Write back the four values the profile had before QuadSens changed them")
        self.restore_button.clicked.connect(self.restore)
        buttons = QtWidgets.QGridLayout()
        buttons.setHorizontalSpacing(SPACE_M)
        buttons.setVerticalSpacing(SPACE_S)
        buttons.addWidget(self.use_button, 0, 0)
        buttons.addWidget(self.reset_button, 0, 1)
        buttons.addWidget(self.apply_button, 1, 0)
        buttons.addWidget(self.restore_button, 1, 1)

        self.flip_check = QtWidgets.QCheckBox("Flip up/dow&n")
        self.flip_check.setToolTip("Exchange the down and up multipliers. " + FLIP_HINT)
        self.flip_check.setChecked(bool(settings.value("rawaccel/flip_y", False, type=bool)))
        self.flip_check.toggled.connect(self._flip_toggled)
        self.flip_hint = caption(WrapLabel("About Flip up/down"))
        self.flip_hint.setText(FLIP_HINT)
        self.advanced = QtWidgets.QWidget()
        advanced_layout = QtWidgets.QVBoxLayout(self.advanced)
        advanced_layout.setContentsMargins(SPACE_M * 2, 0, 0, 0)
        advanced_layout.setSpacing(SPACE_S)
        advanced_layout.addWidget(self.flip_check)
        advanced_layout.addWidget(self.flip_hint)
        self.advanced_toggle = Disclosure("Ad&vanced", self.advanced)
        self.advanced_toggle.setToolTip("Show the switch that is rarely needed: Flip up/down")
        self.advanced_toggle.setChecked(self.flip_check.isChecked())

        for control in (self.choose_button, self.use_button, self.reset_button,
                        self.apply_button, self.restore_button, self.flip_check,
                        self.advanced_toggle):
            control.setAccessibleDescription(control.toolTip())

        self.message = MessageLabel("RawAccel message")

        layout = self.card_layout()
        head = QtWidgets.QHBoxLayout()
        head.addWidget(heading("RawAccel"), 1)
        head.addWidget(self.advanced_toggle)
        layout.addLayout(head)
        layout.addLayout(folder_row)
        layout.addLayout(profile_row)
        layout.addLayout(tables)
        layout.addWidget(self.state_label)
        layout.addWidget(self.note_label)
        layout.addLayout(buttons)
        layout.addWidget(self.message)
        layout.addWidget(self.boot_label)
        layout.addWidget(self.advanced)
        layout.addStretch(1)

        stored = settings.value("rawaccel/folder", "", type=str)
        if stored and Path(stored).is_dir():
            self.set_folder(stored, remember=False)
        else:
            guesses = rawaccel.guess_folders(self._environ)
            if guesses:
                self.set_folder(guesses[0], remember=False)
                self.message.show_message(
                    f"Found RawAccel in {guesses[0]}. Choose another folder if that is "
                    "not the one you use.")
            else:
                self._refresh()

    # ---- values ---------------------------------------------------------------

    def values(self) -> Scalars:
        return Scalars(*(round(spin.value(), DECIMALS) for spin in self.spins))

    def set_values(self, scalars: Scalars) -> None:
        for spin, value in zip(self.spins, scalars.as_tuple()):
            spin.setValue(value)

    def flip(self) -> bool:
        return bool(self.flip_check.isChecked())

    def values_ready(self) -> bool:
        """True once the multipliers are something other than four times 1.0."""
        return not self.values().is_close(Scalars.unity(), 5e-5)

    def is_busy(self) -> bool:
        return self._process is not None

    def use_result(self) -> None:
        result = self._get_result()
        if result is None:
            self.message.show_message(
                "There is no result to take. Import a values file (File menu) or type "
                "the multipliers.", error=True)
            return
        try:
            result.scalars.validate()
        except ValueError as exc:
            self.message.show_message(f"Result not used: {exc}", error=True)
            return
        # Flip is applied in the mapping, not here, so that it also covers typed values.
        self.set_values(driver_scalars(result.scalars, False))
        self.message.show_message(f"Filled from {result.source}. Check the preview, then "
                                  "press Apply.")

    def _flip_toggled(self, checked: bool) -> None:
        self._settings.setValue("rawaccel/flip_y", checked)
        self._refresh()

    # ---- folder and file ----------------------------------------------------------

    def choose_folder(self, folder: str = "") -> None:
        """Take ``folder``; without one, ask for it."""
        if not folder:
            start = str(self.folder) if self.folder is not None else ""
            folder = QtWidgets.QFileDialog.getExistingDirectory(
                self, "Choose the RawAccel folder", start)
            if not folder:
                return
        self.set_folder(folder)

    def set_folder(self, folder: str | os.PathLike[str], remember: bool = True) -> None:
        self.folder = Path(folder)
        self.folder_edit.setText(str(self.folder))
        self.folder_edit.setToolTip(str(self.folder))
        if remember:
            self._settings.setValue("rawaccel/folder", str(self.folder))
        self.message.show_message("")
        self.reload()

    def reload(self) -> None:
        """Read settings.json of the chosen folder again."""
        self.document, self.problem = None, ""
        if self.folder is None:
            self._refresh()
            return
        try:
            self.document = rawaccel.read(rawaccel.settings_path(self.folder))
        except RawAccelError as exc:
            self.problem = str(exc)
        names = self.document.profile_names if self.document is not None else []
        wanted = self.profile_combo.currentText()
        self.profile_combo.blockSignals(True)
        self.profile_combo.clear()
        self.profile_combo.addItems(names)
        if wanted in names:
            self.profile_combo.setCurrentIndex(names.index(wanted))
        self.profile_combo.blockSignals(False)
        self.profile_combo.setEnabled(len(names) > 1)
        self._refresh()

    def profile_index(self) -> int:
        return max(0, self.profile_combo.currentIndex())

    def _key(self) -> str:
        assert self.folder is not None and self.document is not None
        return rawaccel.baseline_key(rawaccel.settings_path(self.folder),
                                     self.document.profile_names[self.profile_index()])

    def _stored(self, what: str) -> Four | None:
        return Four.from_list(self._settings.value(f"rawaccel/{what}/{self._key()}"))

    def _store(self, what: str, four: Four) -> None:
        self._settings.setValue(f"rawaccel/{what}/{self._key()}",
                                [rawaccel.number_text(v) for v in four.as_tuple()])

    def current(self) -> Four | None:
        """The four values of the selected profile as they are in the file."""
        if self.document is None:
            return None
        try:
            return self.document.values(self.profile_index())
        except RawAccelError:
            return None

    def baseline(self) -> Four | None:
        """The profile's own values, from before QuadSens changed anything."""
        current = self.current()
        if current is None:
            return None
        baseline, self.baseline_note = rawaccel.resolve_baseline(
            current, self._stored("baseline"), self._stored("written"))
        return baseline

    def new(self) -> Four | None:
        baseline = self.baseline()
        if baseline is None:
            return None
        try:
            return rawaccel.map_values(baseline, self.values(), self.flip())
        except RawAccelError:
            return None

    def is_applied(self) -> bool:
        """True when the file already holds the new values."""
        current, new = self.current(), self.new()
        return current is not None and new is not None and current.is_close(new, 2e-6)

    def is_neutral(self) -> bool:
        """True when the file holds the profile's own values: nothing of ours is in it."""
        return self.document is not None and self.current() is not None \
            and not self.can_restore()

    def can_restore(self) -> bool:
        current = self.current()
        if current is None or self.document is None:
            return False
        stored = self._stored("baseline")
        return stored is not None and not current.is_close(stored, 2e-6)

    # ---- showing ------------------------------------------------------------------

    def _refresh(self) -> None:
        document = self.document
        current = self.current()
        new = self.new()
        if document is not None:
            self.version_label.setText(
                f"settings of RawAccel {document.version or document.schema.name} "
                f"({document.schema.name} names)")
            labels = document.schema.labels
        else:
            self.version_label.setText("")
            labels = (NO_VALUE,) * 4
        for index in range(4):
            self.setting_labels[index].setText(labels[index])
            self.current_labels[index].setText(
                NO_VALUE if current is None else rawaccel.number_text(current.as_tuple()[index]))
            changed = (current is not None and new is not None and abs(
                new.as_tuple()[index] - current.as_tuple()[index]) > 1e-6)
            text = NO_VALUE if new is None else rawaccel.number_text(new.as_tuple()[index])
            self.new_labels[index].setText(text + ("  ≠" if changed else ""))
            font = self.new_labels[index].font()
            font.setBold(changed)
            self.new_labels[index].setFont(font)
        if self.problem:
            self.state_label.setText("")
            self.note_label.setText("")
            if not self.message.is_error:
                self.message.show_message(self.problem, error=True)
        elif document is None:
            self.state_label.setText("Choose the RawAccel folder to see the preview.")
            self.note_label.setText("")
        else:
            if self.is_applied():
                self.state_label.setText("✓ settings.json holds these values.")
            else:
                self.state_label.setText("≠ Not applied yet: the New column differs from "
                                         "the file.")
            note = rawaccel.accuracy_note(document, self.profile_index())
            if self.baseline_note:
                note = self.baseline_note + " " + note
            self.note_label.setText(note)
        idle = not self.is_busy()
        usable = document is not None and current is not None
        self.apply_button.setEnabled(idle and usable and not self.is_applied())
        self.restore_button.setEnabled(idle and usable and self.can_restore())
        self.choose_button.setEnabled(idle)
        self.changed.emit()

    # ---- writing ------------------------------------------------------------------

    def apply(self) -> None:
        """Write the new values and let RawAccel load them."""
        self._write(restore=False)

    def restore(self) -> None:
        """Write the profile's own values back."""
        self._write(restore=True)

    def _write(self, restore: bool) -> None:
        if self.is_busy() or self.folder is None:
            return
        path = rawaccel.settings_path(self.folder)
        try:
            # Read again right before writing: RawAccel may have saved meanwhile.
            self.document = rawaccel.read(path)
            index = self.profile_index()
            current = self.document.values(index)
            stored = self._stored("baseline")
            baseline, _note = rawaccel.resolve_baseline(current, stored,
                                                        self._stored("written"))
            first_time = stored is None or not baseline.is_close(stored)
            target = baseline if restore else rawaccel.map_values(
                baseline, self.values(), self.flip())
            text = rawaccel.patch(self.document, index, target)
            backup = rawaccel.write(path, text, backup=first_time and not restore)
        except RawAccelError as exc:
            self.message.show_message(f"Nothing was changed: {exc}", error=True)
            self.reload()
            return
        self._store("baseline", baseline)
        self._store("written", target)
        self._settings.sync()
        self._what = "restored" if restore else "written"
        self._backup = backup
        self.reload()
        command = rawaccel.writer_command(self.folder)
        if command is None:
            self._report(rawaccel.describe_writer(rawaccel.MISSING))
            return
        process = QtCore.QProcess(self)
        process.setWorkingDirectory(str(self.folder))
        process.setProcessChannelMode(QtCore.QProcess.ProcessChannelMode.MergedChannels)
        process.finished.connect(self._writer_finished)
        process.errorOccurred.connect(self._writer_error)
        self._process = process
        self.message.show_message("Settings written. Waiting for writer.exe ...")
        self._refresh()
        self._timer.start()
        process.start(command[0], command[1:])

    def _report(self, result: rawaccel.WriterResult) -> None:
        what = "The original values are back in settings.json." if self._what == "restored" \
            else "The new values are in settings.json."
        if self._backup is not None:
            what += f" The original file was kept as {self._backup.name}."
        if result.ok:
            self.message.show_message(f"✓ {what} {result.message}")
        else:
            self.message.show_message(f"{what} {result.message}",
                                      error=result.kind == rawaccel.FAILED, kind="warning")
        self._refresh()

    def _release(self) -> None:
        self._timer.stop()
        if self._process is not None:
            self._process.deleteLater()
        self._process = None

    def _writer_finished(self, exit_code: int, _status: QtCore.QProcess.ExitStatus) -> None:
        process = self._process
        if process is None:
            return
        output = bytes(process.readAll().data()).decode("utf-8", "replace")
        self._release()
        kind = rawaccel.APPLIED if exit_code == 0 else rawaccel.FAILED
        self._report(rawaccel.describe_writer(kind, exit_code, output))

    def _writer_error(self, error: QtCore.QProcess.ProcessError) -> None:
        if error != QtCore.QProcess.ProcessError.FailedToStart or self._process is None:
            return
        self._release()
        self._report(rawaccel.describe_writer(rawaccel.NOT_STARTED))

    def _writer_slow(self) -> None:
        """writer.exe is still open after the timeout: most likely its message box."""
        if self._process is not None:
            self._report(rawaccel.describe_writer(rawaccel.TIMEOUT))
