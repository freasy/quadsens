# USB/IP spike

This is a throwaway test, not a feature. It answers one question: if the
virtual mouse is offered over USB/IP instead of Linux' `/dev/uhid`, does a
USB/IP client attach it as a USB device, and does the game (Aimlabs) then show
the per-direction calibration?

`usbip_spike.py` is a USB/IP **server**: it plays the machine that owns a USB
device, and exports one emulated HID device (vendor 1B1C, product 2B43, a
Corsair SABRE V3 PRO as far as the host can tell). A USB/IP **client** attaches
it; the operating system then sees an ordinary USB device. The answers to the
mouse's host protocol come from the same code the Linux version uses
(`quadsens/emulator.py`); only the transport is new.

It listens on 127.0.0.1 port 3240 only. It moves no pointer.

Nothing here has been attached to a real client yet. The built-in self-test
only proves that the program talks to its own small client.

## Check that it runs at all

    quadsens-usbip-spike.exe --selftest        (Windows)
    python3 tools/usbip_spike/usbip_spike.py --selftest

It prints 16 lines ending in `SELFTEST PASS`. No driver is needed for this.

## Windows

1. Install the USB/IP client **usbip-win2**:
   <https://github.com/vadimgrn/usbip-win2/releases>. The research for this
   spike referred to version 0.9.8.1; check for the current one. Its installer
   needs administrator rights, installs a driver, and restarts the USB hubs
   while doing so (USB devices drop out for a moment).
2. Start the spike in a terminal and leave it running:

       quadsens-usbip-spike.exe --log spike-log.jsonl

   It prints `listening on 127.0.0.1:3240, busid 1-1`.
3. In a second terminal:

       usbip list -r 127.0.0.1
       usbip attach -r 127.0.0.1 -b 1-1

   The first command should list one device `1-1` with `1b1c:2b43`.
4. Look at Device Manager, "Human Interface Devices": there should be a new
   *HID-compliant vendor-defined device* (and a *USB Input Device* above it).
   Its hardware ids (Properties > Details > Hardware Ids) contain
   `VID_1B1C&PID_2B43`.
5. Start the game and look for the per-direction calibration entry in its
   Mouselabs section. If it is there, run the calibration.
6. Watch the spike's terminal. A big `### DIRECTIONAL VALUE WRITTEN` banner
   appears for each of the four values the game writes.
7. Detach and clean up:

       usbip port                    (shows the port number in use)
       usbip detach -p <port>        (or: usbip detach -a)

   Stop the spike with Ctrl+C. usbip-win2 is removed through "Installed apps"
   in the Windows settings.

Variations worth one try each if the game does not show the entry:
`--layout two-interfaces` (a second HID interface like the Linux version's
second device), and `--pid 0x2B44`.

## Linux, as a cross-check

The same server can be attached with the kernel's own USB/IP client. This
needs root and the `usbip` tool (Arch/Manjaro: package `usbip`).

    python3 tools/usbip_spike/usbip_spike.py
    sudo modprobe vhci-hcd
    sudo usbip list -r 127.0.0.1
    sudo usbip attach -r 127.0.0.1 -b 1-1
    lsusb -d 1b1c:2b43 -v            (descriptors as the kernel read them)
    sudo usbip port
    sudo usbip detach -p <port>

After the attach the device should behave like the uhid one: a hidraw node
appears, and with the udev rule and the Steam launch option from the README
the game should find it. If this works on Linux and not on Windows, the
problem is on the Windows side, not in the server.

## What to report back

| Observation                                              | Windows | Linux |
|----------------------------------------------------------|---------|-------|
| `--selftest` prints PASS                                 |         |       |
| `usbip list -r 127.0.0.1` shows `1-1 ... 1b1c:2b43`       |         |       |
| `usbip attach` succeeds (no error, port shown)           |         |       |
| Device visible (Device Manager / `lsusb`)                |         |       |
| The spike log shows control requests after the attach    |         |       |
| The game shows the calibration entry                     |         |       |
| The spike log shows protocol traffic (`hid` lines)       |         |       |
| The four values were written (banner lines)              |         |       |
| The game wrote its result file                           |         |       |
| Detach leaves the spike running, attach again works      |         |       |

Please also keep the terminal output or `spike-log.jsonl`, above all the last
lines before anything went wrong. An entry `STALL` in the log names a request
the device does not answer; those are the first thing to look at.

## Known unknowns

- Which requests Windows sends during enumeration beyond the ones handled
  (device, configuration, string, HID and report descriptors, the usual
  SET/GET requests). Unknown requests are answered with a stall and logged.
- Whether usbip-win2 accepts the fields this server sends where the protocol
  document and real traffic differ (`start_frame`, `number_of_packets` in
  replies are sent as 0).
- Whether the game on Windows looks at more than the first HID collection, at
  the interface number, or at strings the Linux version never had to present
  (manufacturer string: "Corsair").
- Whether the game sends its commands as output reports on the interrupt
  endpoint or through SET_REPORT; both are handled, the log shows which.
- Timing: replies are sent as soon as a request for them is waiting; nothing
  is paced to the 1 ms polling interval.
- Nothing is known about anti-cheat or driver-signing reactions to a USB/IP
  bus driver being installed.
