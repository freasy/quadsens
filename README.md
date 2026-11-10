# QuadSens

A small Linux desktop app (Python 3, Qt Widgets) that ties three steps together:

1. **Virtual mouse** – creates a virtual HID device through `/dev/uhid` that
   identifies as a Corsair SABRE V3 PRO and answers its host protocol, so that
   Aimlabs offers its directional sensitivity (PureVector/OmniSense) calibration
   even though no such mouse is connected. Decoded commands are shown live.
2. **Calibration** – shows the newest result file (`ASFSettings.json`) next to
   the values the game wrote to the virtual mouse in this session, including a
   compass plot of the eight directional scalars. Refreshes when the file changes.
3. **Driver (yeetmouse)** – reads the four `DirScale*` parameters of the patched
   [yeetmouse](https://github.com/AndyFilter/YeetMouse) kernel module, lets you
   edit them, fill them from the calibration and apply them.

**The emulated device returns made-up values.** Battery level, firmware
version, DPI stages and everything else it reports are invented. It moves no
pointer and exists only so that the game offers the calibration.

QuadSens is an independent project, not affiliated with or endorsed by Corsair or Aimlabs. All trademarks belong to their owners.

![Screenshot](docs/screenshot.png)

## Requirements

- Linux with the `uhid` kernel module, Python 3.10 or newer
- PySide6 (Arch/Manjaro: `sudo pacman -S pyside6`). PyQt6 is used as a fallback
  if PySide6 is missing.
- The patched yeetmouse module exposing `DirScaleXPos`, `DirScaleXNeg`,
  `DirScaleYPos` (+Y = down), `DirScaleYNeg` and `update` under
  `/sys/module/yeetmouse/parameters`
- `pkexec` (polkit), only if those parameters are root-only on your system
- Membership in group `input` plus the udev rule below, for the virtual mouse

## Run

```sh
cd quadsens
python3 -m quadsens            # from the checkout
# or install it: pip install --user .   -> command "quadsens"
```

`quadsens.desktop` can be copied to `~/.local/share/applications/` once the
`quadsens` command is installed. `python3 -m quadsens.emulator` runs only the
virtual mouse in a terminal.

Typical use: start the virtual mouse, start the game (Steam launch option
`PROTON_ENABLE_HIDRAW=0x1B1C/0x2B43 %command%`), run the calibration there,
then press *Use calibration* and *Apply*. If up and down feel swapped
afterwards, toggle *Flip Y* and apply again: which of the game's two Y values
means "down" is not confirmed.

## udev rule (once, needs root)

`99-quadsens.rules` gives group `input` access to `/dev/uhid` and to the hidraw
nodes of the virtual device. Without it neither the app nor Proton can open them.

```sh
sudo install -m 0644 99-quadsens.rules /etc/udev/rules.d/99-quadsens.rules
sudo udevadm control --reload
sudo udevadm trigger
id -nG | grep -w input     # you must be in group "input"
```

Remove it again with `sudo rm /etc/udev/rules.d/99-quadsens.rules` followed by
the same two `udevadm` commands.

## Writing the driver parameters

If the parameter files are writable by your user, *Apply* writes them directly.
Otherwise it runs `pkexec helper/quadsens-apply XPOS XNEG YPOS YNEG` once. The
helper accepts exactly four plain decimal numbers between 0.1 and 10 and writes
only the four fixed parameter files and `update`.

When run from a checkout, pkexec executes the helper from your home directory.
For a root-owned copy install it; the app prefers that location:

```sh
sudo install -m 0755 helper/quadsens-apply /usr/local/libexec/quadsens-apply
```

## Tests

```sh
python3 -m pytest                          # or:
python3 -m unittest discover -s tests -t .
python3 tools/make_screenshot.py           # offscreen smoke test + docs/screenshot.png
```

The tests never open `/dev/uhid`, run pkexec or write to `/sys`. The UI tests
are skipped when no Qt binding is installed.

## Limitations

- The device is an emulation built from static analysis of the protocol; whether
  a given game or Proton version accepts it is not guaranteed.
- Only the four axis scalars reach the driver. The eight directional scalars are
  displayed but not applied.
- The Y sign convention between the calibration and the driver is unconfirmed
  (hence *Flip Y*).
- New values are entered with four decimals.
- If the parameters are root-only they cannot be read back, so the app cannot
  confirm what the driver reports after applying.
- "Apply saved values on start" may show a polkit prompt at startup on such systems.
