# QuadSens

QuadSens is a small Linux desktop tool for people who use the
[yeetmouse](https://github.com/AndyFilter/YeetMouse) mouse driver and want a
separate sensitivity multiplier for each direction (right, left, down, up).
The aim trainer Aimlabs can measure such multipliers, but it only offers that
calibration when it finds one specific mouse model. QuadSens presents a
virtual mouse that identifies as that model, so the calibration can be run
with any mouse, then reads the result and writes the four multipliers to a
patched yeetmouse kernel module.

![QuadSens main window](docs/screenshot.png)

The screenshot shows fixture data. A dark-theme rendering is in
[docs/screenshot-dark.png](docs/screenshot-dark.png).

## How it works

1. **Virtual mouse.** QuadSens creates a virtual HID device through
   `/dev/uhid`. It identifies as a Corsair SABRE V3 PRO and answers that
   mouse's configuration protocol, which is what makes Aimlabs offer its
   per-direction sensitivity calibration (called PureVector or OmniSense
   there). The device never moves the pointer.
2. **Calibration result.** The calibration produces four multipliers (right,
   left, down, up) and eight compass values. QuadSens reads them from the
   result file Aimlabs writes (`ASFSettings.json` in its Proton prefix) and
   from what Aimlabs sends to the virtual mouse, and shows both side by side.
3. **Driver.** QuadSens writes the four multipliers to the `DirScaleXPos`,
   `DirScaleXNeg`, `DirScaleYPos` and `DirScaleYNeg` parameters of the patched
   yeetmouse module. It writes directly if your user is in group `yeetmouse`,
   otherwise through one `pkexec` call of a small helper script.

## Requirements

- Linux with the `uhid` kernel module (part of every mainstream distribution
  kernel).
- The yeetmouse fork with directional scaling:
  <https://github.com/freasy/YeetMouse>, branch `feature/directional-scalars`.
  Its
  [DIRECTIONAL_SCALING.md](https://github.com/freasy/YeetMouse/blob/feature/directional-scalars/DIRECTIONAL_SCALING.md)
  explains the parameters and the installation. The unmodified yeetmouse does
  not have these parameters.
- Aimlabs, installed through Steam and run with Proton.
- Read/write access to `/dev/uhid` for your user (see
  [One-time setup](#one-time-setup)).
- Only for running from source: Python 3.10 or newer with PySide6. PyQt6 is
  used as a fallback if PySide6 is missing. The binary needs neither.
- Only for the `pkexec` fallback: polkit.

## Install

### Binary

Download the `quadsens` binary from the
[Releases page](https://github.com/freasy/quadsens/releases), make it
executable and run it:

```sh
chmod +x quadsens
./quadsens
```

There may be no release yet. In that case build the binary yourself, which
takes one command.

The one-time setup below needs two files from this repository
(`99-quadsens.rules` and, optionally, `helper/quadsens-apply`), so clone it or
download those files as well.

### Build the binary from source

```sh
git clone https://github.com/freasy/quadsens.git
cd quadsens
./build.sh
```

`build.sh` creates a virtual environment in `.venv`, installs
PySide6-Essentials and PyInstaller into it from PyPI and writes a single-file
binary to `dist/quadsens`. It needs Python 3.10 or newer with the `venv`
module and does not install anything system-wide.

### Run from source

```sh
git clone https://github.com/freasy/quadsens.git
cd quadsens
python3 -m quadsens
```

This needs PySide6 from your distribution (Arch/Manjaro:
`sudo pacman -S pyside6`).

### Menu entry (optional)

```sh
install -Dm755 dist/quadsens ~/.local/bin/quadsens
install -Dm644 assets/quadsens.svg ~/.local/share/icons/hicolor/scalable/apps/quadsens.svg
install -Dm644 quadsens.desktop ~/.local/share/applications/quadsens.desktop
```

The menu entry starts the command `quadsens`, so `~/.local/bin` has to be in
your `PATH`.

## One-time setup

### Access to /dev/uhid

`99-quadsens.rules` gives group `input` access to `/dev/uhid` and to the
hidraw nodes of the virtual device. Without it neither QuadSens nor Proton can
open them.

```sh
sudo install -m 0644 99-quadsens.rules /etc/udev/rules.d/99-quadsens.rules
sudo udevadm control --reload
sudo udevadm trigger
sudo usermod -aG input "$USER"
```

Log out and in again, then check with `id -nG` that `input` is listed.

To undo: `sudo rm /etc/udev/rules.d/99-quadsens.rules`, followed by the same
two `udevadm` commands.

### Access to the driver parameters

The fork's `yeetmouse.service` hands the parameter files in
`/sys/module/yeetmouse/parameters/` to group `yeetmouse`. Join that group:

```sh
sudo usermod -aG yeetmouse "$USER"
```

Log out and in again. After that QuadSens reads and writes the parameters
without asking for a password.

### Helper for the pkexec fallback (optional)

If you are not in group `yeetmouse`, *Apply* can write the values through
`pkexec` instead. For that the helper script has to be installed system-wide:

```sh
sudo install -D -m 0755 helper/quadsens-apply /usr/local/libexec/quadsens-apply
```

The helper accepts exactly four plain decimal numbers between 0.1 and 10 and
writes only the four parameter files and `update`. The binary uses the helper
only from a system location (`/usr/local/libexec`, `/usr/libexec` or
`/usr/lib/quadsens`). When run from a checkout, QuadSens also falls back to
`helper/quadsens-apply` in the checkout. Without group membership the current
driver values cannot be read, so QuadSens cannot confirm what it wrote.

### Steam launch option

Proton has to pass the virtual device through to the game. Set this launch
option for Aimlabs in Steam (Properties > General > Launch options):

```
PROTON_ENABLE_HIDRAW=0x1B1C/0x2B43 %command%
```

`0x2B43` is the default product ID in QuadSens. If you choose another one in
the *Product ID* box, use the same value here.

## Usage

1. Start QuadSens and press *Start virtual mouse*. The status next to the
   button changes to "Running: waiting for a client".
2. In the *Driver* panel press *Reset to 1.0* and then *Apply*, so that all
   four values are 1 and the calibration measures the unscaled mouse.
3. Start Aimlabs. The status changes to "Running: device opened by a client"
   once the game has found the virtual mouse.
4. In Aimlabs open the Mouselabs section and run the per-direction
   sensitivity calibration with your real mouse.
5. Back in QuadSens the *Calibration* panel shows the result: the four
   multipliers from the result file, the values written to the virtual mouse
   and a compass plot of the eight directional values. The command log marks
   the relevant lines with `>>> DIRECTIONAL WRITE`.
6. Press *Use calibration*. The four new values are filled in and the changed
   rows are marked as unsaved.
7. Press *Apply*. The *Driver reports* column shows what the module now uses.
8. If up and down feel swapped afterwards, tick *Flip Y* and press *Apply*
   again.
9. Values applied this way last until the module is reloaded or the machine
   reboots. To keep them, save the driver's current values with the fork's
   tool:

   ```sh
   sudo yeetmousectl save /etc/yeetmouse.conf
   ```

   `yeetmouse.service` applies that file at boot.

*Apply saved on start* makes QuadSens write the last applied values again
whenever it starts. *File > Open result folder* (Ctrl+O) opens the folder of
the result file, and *Help > First steps* (F1) shows this procedure in the
application.

The virtual mouse can also be run without the window:
`python3 -m quadsens.emulator` (options: `--pid`, `--state`, `--layout`).

QuadSens keeps its settings in `~/.config/QuadSens/QuadSens.conf` and the
state of the virtual mouse in
`~/.local/state/quadsens/virtual-mouse-state.json`.

## What this does and does not do

- The virtual device only identifies as that mouse and answers its
  configuration protocol. Every value it reports (battery level, firmware
  version, DPI stages and so on) is made up.
- No real hardware is modified. Nothing is written to any physical mouse.
- No game files are modified. QuadSens reads one result file and never writes
  to the game's directories.
- Presenting a virtual device to software that expects particular hardware may
  be against the terms of the software it interoperates with. Checking that is
  your responsibility.
- The multipliers are calibrated for your own aim on your own setup. They are
  not validated by any vendor, and there is no guarantee that they improve
  anything.

## Limitations and status

- Tested end to end on one machine: Manjaro, KDE Plasma, Aimlabs under Steam
  Proton. Other distributions, desktops and Proton versions are untested.
- Which of the calibration's two Y values means "down" is not confirmed. The
  *Flip Y* toggle exchanges them.
- Only the four axis multipliers reach the driver. The eight compass values
  are displayed but not applied.
- New values are entered with four decimals.
- The virtual device is built from an analysis of the protocol. A future
  version of the game or of Proton may stop accepting it.
- Windows is not supported yet.

## Troubleshooting

**The calibration entry is not visible in Aimlabs.**
Start the virtual mouse before the game. Check that the Steam launch option is
set and that its product ID matches the one selected in QuadSens. Check that
the udev rule is installed and that you are in group `input`; without it
Proton cannot open the hidraw node. While the game is running, the status in
QuadSens should read "Running: device opened by a client" and the command log
should show traffic. If it stays at "waiting for a client", the game has not
found the device.

**"/dev/uhid is not writable" or "cannot create uhid device".**
The udev rule is missing or you are not in group `input` in the current
session. See [One-time setup](#one-time-setup) and log in again. If
`/dev/uhid` does not exist at all, load the module with `sudo modprobe uhid`.

**The driver panel says "not readable".**
Your session is not in group `yeetmouse`. Join the group and log out and in
again; `id -nG` must list it.

**It worked, then it says "not readable" again after reloading the module.**
The parameter files are created as `root:root` every time the module is
loaded. `yeetmouse.service` sets the group, so run it again:

```sh
sudo systemctl restart yeetmouse.service
```

**The driver panel says "module not loaded" or "no DirScale parameters".**
Load the module (`sudo modprobe yeetmouse`). If the parameters are still
missing, the loaded module is the unmodified yeetmouse and not the fork.

**Apply reports "helper script quadsens-apply not found".**
You are not in group `yeetmouse` and the helper is not installed system-wide.
Do one of the two (see [One-time setup](#one-time-setup)).

**The values are back to 1 after a reboot.**
They were applied but not saved. Run `sudo yeetmousectl save
/etc/yeetmouse.conf` after applying, or tick *Apply saved on start*.

## Development

Run the tests from the repository root:

```sh
QT_QPA_PLATFORM=offscreen python3 -m unittest discover -s tests -t .
```

The tests never open `/dev/uhid`, run `pkexec` or write to `/sys`. The UI
tests are skipped when no Qt binding is installed. To regenerate the
screenshots from fixture data:

```sh
python3 tools/make_screenshot.py
python3 tools/make_screenshot.py --dark
```

Layout:

```
quadsens/              application package
  emulator.py          virtual HID mouse and protocol (no Qt)
  calibration.py       find and parse the result file (no Qt)
  driver.py            read and write the module parameters (no Qt)
  model.py             value types and number parsing (no Qt)
  hints.py, about.py   user-facing texts (no Qt)
  resources.py         locate bundled files (no Qt)
  ui/                  Qt widgets; ui/qt.py selects PySide6 or PyQt6
helper/quadsens-apply  helper run through pkexec
assets/quadsens.svg    application icon
packaging/entry.py     entry point for the PyInstaller build
tools/make_screenshot.py
tests/
99-quadsens.rules      udev rule
quadsens.desktop       menu entry
build.sh               builds dist/quadsens
```

## License

GPL-3.0-or-later. See [LICENSE](LICENSE).

QuadSens is an independent project, not affiliated with or endorsed by Corsair or Aimlabs. All trademarks belong to their owners.
