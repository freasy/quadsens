"""Tests of the Windows virtual mouse without Windows: server thread, usbip-win2
client handling with a fake command runner, and the two together. The "driver"
is the in-process USB/IP client that ships with the server module.
Platform-independent; also run on Windows in CI.
"""

from __future__ import annotations

import socket
import struct
import threading
import time
import unittest

from quadsens import emulator, usbip_client, usbip_mouse, usbip_server
from quadsens.usbip_client import UsbipClient
from quadsens.usbip_server import Client, VirtualMouseServer

PROGRAM_FILES = {"ProgramFiles": "C:\\Program Files", "SystemRoot": "C:\\Windows",
                 "ProgramFiles(x86)": "C:\\Program Files (x86)"}
TOOL = "C:\\Program Files\\USBip\\usbip.exe"
PORT_OUTPUT = """Imported USB devices
====================
Port 01: device in use at Full Speed(12Mbps)
         unknown vendor : unknown product (046d:c077)
           -> usbip://192.168.1.9:3240/3-2
           -> remote bus/dev: 003/004
           -> serial:
           -> mode: event
Port 03: device in use at Full Speed(12Mbps)
         unknown vendor : unknown product (1b1c:2b43)
           -> usbip://127.0.0.1:{port}/1-1
           -> remote bus/dev: 001/002
           -> serial: EMU0
           -> mode: event
"""


def wait_until(condition, seconds: float = 5.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.01)
    return bool(condition())


class FakeDriver:
    """Stands in for usbip.exe and its driver: a command runner that really attaches.

    "attach" connects the in-process USB/IP client to the server and imports
    the device, as the Windows driver would; "detach -p" closes that
    connection; "port" lists it together with somebody else's device.
    """

    def __init__(self, server_port: int = 0) -> None:
        self.server_port = server_port
        self.calls: list[list[str]] = []
        self.client: Client | None = None
        self.attach_error = ""         # text usbip.exe would print instead of attaching
        self.version = "0.9.7.7"

    def __call__(self, argv: list[str], _timeout: float) -> tuple[int, str]:
        args = argv[1:]
        self.calls.append(args)
        if args and args[0] == "-t":
            self.server_port = int(args[1])
            args = args[2:]
        if args == ["--version"]:
            return 0, self.version + "\n"
        if args[:1] == ["port"]:
            if self.client is None:
                return 0, PORT_OUTPUT.split("Port 03")[0]
            return 0, PORT_OUTPUT.format(port=self.server_port)
        if args[:1] == ["attach"]:
            if "--stop" in args:
                return 0, ""
            if self.attach_error:
                return 1, self.attach_error
            self.client = Client("127.0.0.1", self.server_port)
            if self.client.import_device(args[args.index("-b") + 1]) != 0:
                return 1, "error: device not found"
            return 0, "3\n" if "--terse" in args else "successfully attached to port 3\n"
        if args[:2] == ["detach", "-p"]:
            if args[2] != "3" or self.client is None:
                return 1, "error: invalid port"
            self.client.close()
            self.client = None
            return 0, "port 3 is successfully detached\n"
        return 1, "unknown command"

    def commands(self) -> list[str]:
        return [" ".join(args) for args in self.calls]


class FindToolTest(unittest.TestCase):
    def test_path_first_then_the_install_folder(self) -> None:
        self.assertEqual(usbip_client.find_tool({}, lambda name: "D:\\x\\usbip.exe"),
                         "D:\\x\\usbip.exe")
        found = usbip_client.find_tool(PROGRAM_FILES, lambda name: None,
                                       lambda path: path == TOOL)
        self.assertEqual(found, TOOL)
        self.assertIsNone(usbip_client.find_tool(PROGRAM_FILES, lambda name: None,
                                                 lambda path: False))
        self.assertIsNone(usbip_client.find_tool({}, lambda name: None, lambda path: True))

    def test_protected_locations(self) -> None:
        for path in (TOOL, "c:\\program files\\usbip\\USBIP.EXE",
                     "C:\\Windows\\System32\\usbip.exe",
                     "C:\\Program Files (x86)\\USBip\\usbip.exe"):
            self.assertTrue(usbip_client.is_protected_location(path, PROGRAM_FILES), path)
        for path in ("C:\\Users\\someone\\Downloads\\usbip.exe", "usbip.exe",
                     "C:\\Program Files\\..\\Users\\x\\usbip.exe",
                     "C:\\Program FilesEvil\\usbip.exe", "D:\\tools\\usbip.exe",
                     "C:\\Temp\\Program Files\\USBip\\usbip.exe"):
            self.assertFalse(usbip_client.is_protected_location(path, PROGRAM_FILES), path)
        self.assertFalse(usbip_client.is_protected_location(TOOL, {}))
        self.assertTrue(usbip_client.can_offer_elevation(TOOL, PROGRAM_FILES))
        self.assertFalse(usbip_client.can_offer_elevation(None, PROGRAM_FILES))
        self.assertFalse(usbip_client.can_offer_elevation(
            "C:\\Users\\x\\usbip.exe", PROGRAM_FILES))


class ClientTest(unittest.TestCase):
    def client(self, replies: dict[str, tuple[int, str]]) -> tuple[UsbipClient, list[str]]:
        calls: list[str] = []

        def runner(argv: list[str], _timeout: float) -> tuple[int, str]:
            line = " ".join(argv[1:])
            calls.append(line)
            for key, reply in replies.items():
                if line.startswith(key):
                    return reply
            return 1, "error"
        return UsbipClient(TOOL, runner), calls

    def test_commands_as_documented(self) -> None:
        client, _calls = self.client({})
        self.assertEqual(client.attach_command(),
                         [TOOL, "attach", "-r", "127.0.0.1", "-b", "1-1", "--once", "--terse"])
        self.assertEqual(usbip_client.command_line(client.attach_command()),
                         '"C:\\Program Files\\USBip\\usbip.exe" attach -r 127.0.0.1 -b 1-1 '
                         '--once --terse')
        other = UsbipClient(TOOL, lambda argv, t: (0, ""), tcp_port=3241)
        self.assertEqual(other.attach_command()[1:3], ["-t", "3241"])
        self.assertEqual(usbip_client.RELEASES_URL,
                         "https://github.com/vadimgrn/usbip-win2/releases")

    def test_version(self) -> None:
        client, calls = self.client({"--version": (0, "0.9.7.7\n")})
        self.assertEqual(client.version(), "0.9.7.7")
        self.assertEqual(calls, ["--version"])
        self.assertEqual(self.client({"--version": (1, "boom")})[0].version(), "")
        self.assertEqual(UsbipClient(None).version(), "")
        self.assertFalse(UsbipClient(None).installed)

    def test_attach_outcomes(self) -> None:
        client, _ = self.client({"attach": (0, "2\n")})
        outcome = client.attach()
        self.assertEqual((outcome.ok, outcome.port), (True, 2))
        client, _ = self.client({"attach": (0, "successfully attached to port 7\n")})
        self.assertEqual(client.attach().port, 7)
        missing = UsbipClient(None).attach()
        self.assertEqual(missing.kind, usbip_client.NOT_INSTALLED)
        self.assertIn("not installed", missing.message)
        self.assertIn("imported or typed", missing.message)
        cases = {
            "error: Access is denied.": (usbip_client.DENIED, "administrator"),
            "error: The system cannot find the file specified.":
                (usbip_client.DRIVER_MISSING, "Install usbip-win2 again"),
            "error: No connection could be made because the target machine actively "
            "refused it.": (usbip_client.UNREACHABLE, "firewall"),
            "error: something odd": (usbip_client.FAILED, "something odd"),
        }
        for text, (kind, expected) in cases.items():
            client, _ = self.client({"attach": (1, text)})
            outcome = client.attach()
            self.assertEqual(outcome.kind, kind, text)
            self.assertIn(expected, outcome.message)
            self.assertFalse(outcome.ok)
        denied = self.client({"attach": (1, "Access is denied.")})[0].attach()
        self.assertIn("usbip.exe\" attach -r 127.0.0.1 -b 1-1", denied.message)

    def test_already_attached_counts_when_it_is_ours(self) -> None:
        client, _ = self.client({"attach": (1, "error: device is already attached"),
                                 "port": (0, PORT_OUTPUT.format(port=3240))})
        outcome = client.attach()
        self.assertEqual((outcome.ok, outcome.port), (True, 3))
        client, _ = self.client({"attach": (1, "error: device is already attached"),
                                 "port": (0, "")})
        self.assertEqual(client.attach().kind, usbip_client.ALREADY)

    def test_only_our_port_is_found_and_detached(self) -> None:
        self.assertEqual(usbip_client.parse_ports(PORT_OUTPUT.format(port=3240)),
                         [(1, "192.168.1.9", 3240, "3-2"), (3, "127.0.0.1", 3240, "1-1")])
        self.assertEqual(usbip_client.parse_ports(""), [])
        client, calls = self.client({"port": (0, PORT_OUTPUT.format(port=3240)),
                                     "attach": (0, ""),
                                     "detach -p 3": (0, "port 3 is successfully detached\n")})
        self.assertEqual(client.our_port(), 3)
        outcome = client.detach()
        self.assertTrue(outcome.ok)
        self.assertEqual(outcome.port, 3)
        self.assertIn("detach -p 3", calls)
        self.assertFalse(any("-a" in call.split() or "--all" in call for call in calls))
        self.assertTrue(any(call.endswith("--stop") for call in calls))
        # Somebody else's device on the same busid of another server is not ours.
        foreign, calls = self.client({"port": (0, PORT_OUTPUT.format(port=9999)),
                                      "attach": (0, "")})
        self.assertEqual(foreign.our_port(), 0)
        self.assertTrue(foreign.detach().ok)
        self.assertFalse(any(call.startswith("detach") for call in calls))

    def test_failed_detach_names_the_manual_command(self) -> None:
        client, _ = self.client({"port": (0, PORT_OUTPUT.format(port=3240)),
                                 "attach": (0, ""), "detach": (1, "Access is denied.")})
        outcome = client.detach()
        self.assertFalse(outcome.ok)
        self.assertIn("detach -p 3", outcome.message)

    def test_runner_never_raises(self) -> None:
        code, output = usbip_client.run_command(["/nonexistent/usbip.exe", "port"], 2.0)
        self.assertEqual(code, 127)
        self.assertTrue(output)
        self.assertFalse(usbip_client.run_elevated(["x"]) if __import__("os").name != "nt"
                         else False)


class ServerThreadTest(unittest.TestCase):
    def test_start_events_and_stop(self) -> None:
        server = VirtualMouseServer(port=0)
        self.addCleanup(server.stop)
        server.start()
        self.assertTrue(server.running)
        self.assertEqual(VirtualMouseServer.HOST, "127.0.0.1")
        client = Client("127.0.0.1", server.port)
        self.assertEqual(client.import_device(), 0)
        self.assertTrue(wait_until(lambda: server.attached))
        request = bytes([0, 1, 0, emulator.CMD_SET]) + struct.pack("<Hf", 0x125, 1.011)
        client.submit(usbip_server.DIR_OUT, 1, 64, data=request.ljust(64, b"\0"))
        client.reply()
        entries: list[dict] = []
        self.assertTrue(wait_until(lambda: entries.extend(server.poll())
                                   or any("highlight" in e for e in entries)))
        self.assertTrue(any(e.get("event") == "client" and e.get("state") == "attached"
                            for e in entries))
        client.close()
        self.assertTrue(wait_until(lambda: not server.attached))
        self.assertTrue(server.stop())
        self.assertFalse(server.running)
        self.assertTrue(any(e.get("state") == "detached" for e in server.poll()))

    def test_port_in_use_is_reported_clearly(self) -> None:
        blocker = socket.socket()
        blocker.bind(("127.0.0.1", 0))
        blocker.listen(1)
        self.addCleanup(blocker.close)
        server = VirtualMouseServer(port=blocker.getsockname()[1])
        with self.assertRaises(usbip_server.ServerError) as caught:
            server.start()
        self.assertIn("in use by another program", str(caught.exception))
        self.assertFalse(server.running)

    def test_stop_with_a_request_still_waiting(self) -> None:
        server = VirtualMouseServer(port=0)
        server.start()
        client = Client("127.0.0.1", server.port)
        client.import_device()
        client.submit(usbip_server.DIR_IN, 1, 64)        # never answered
        self.assertTrue(wait_until(lambda: server.attached))
        started = time.monotonic()
        self.assertTrue(server.stop())
        self.assertLess(time.monotonic() - started, 2.5)
        self.assertFalse(server.running)
        with self.assertRaises((ConnectionError, OSError)):
            client.read(1)                               # the connection was closed
        client.close()
        again = VirtualMouseServer(port=0)               # and a new one can be started
        again.start()
        self.assertTrue(again.stop())

    def test_start_twice_and_stop_twice(self) -> None:
        server = VirtualMouseServer(port=0)
        server.start()
        port = server.port
        server.start()
        self.assertEqual(server.port, port)
        self.assertTrue(server.stop())
        self.assertTrue(server.stop())


class VirtualMouseTest(unittest.TestCase):
    def make(self, tool: str | None = TOOL, environ: dict | None = None):
        driver = FakeDriver()
        self.elevated: list[list[str]] = []
        mouse = usbip_mouse.VirtualMouse(
            UsbipClient(tool, driver), VirtualMouseServer(port=0),
            elevate=lambda argv: self.elevated.append(argv) or True,
            environ=PROGRAM_FILES if environ is None else environ)
        self.addCleanup(mouse.close)
        return mouse, driver

    def settle(self, mouse) -> list[dict]:
        self.assertTrue(wait_until(lambda: not mouse.busy))
        time.sleep(0.05)
        return mouse.poll()

    def test_start_attaches_and_stop_detaches_only_our_port(self) -> None:
        mouse, driver = self.make()
        self.assertEqual(mouse.state, usbip_mouse.STOPPED)
        self.assertTrue(mouse.start())
        self.settle(mouse)
        self.assertEqual(mouse.state, usbip_mouse.ATTACHED)
        self.assertEqual(mouse.port, 3)
        self.assertIn("Attached to USB/IP port 3", mouse.message)
        self.assertIsNotNone(mouse.started_at)
        attach = [c for c in driver.commands() if "attach" in c][0]
        self.assertIn("attach -r 127.0.0.1 -b 1-1 --once --terse", attach)

        # A program talks to the device: the state says so.
        assert driver.client is not None
        get = bytes([0, 1, 0, emulator.CMD_GET]) + struct.pack("<H", 0x13)
        driver.client.submit(usbip_server.DIR_OUT, 1, 64, data=get.ljust(64, b"\0"))
        driver.client.reply()
        self.assertTrue(wait_until(lambda: mouse.poll() is not None
                                   and mouse.state == usbip_mouse.IN_USE))

        mouse.stop(wait=True)
        self.assertEqual(mouse.state, usbip_mouse.STOPPED)
        self.assertFalse(mouse.running)
        commands = driver.commands()
        self.assertTrue(any(c.endswith("detach -p 3") for c in commands))
        self.assertFalse(any(" -a" in c or "--all" in c for c in commands))
        self.assertIsNone(driver.client)
        self.assertEqual(mouse.port, 0)

    def test_without_usbip_win2_the_server_still_runs(self) -> None:
        mouse, driver = self.make(tool=None)
        self.assertTrue(mouse.start())
        self.settle(mouse)
        self.assertEqual(mouse.state, usbip_mouse.SERVING)
        self.assertEqual(mouse.problem, usbip_client.NOT_INSTALLED)
        self.assertIn("not installed", mouse.message)
        self.assertEqual(driver.calls, [])
        self.assertEqual(mouse.attach_command_text(), "")
        mouse.close()
        self.assertEqual(mouse.state, usbip_mouse.STOPPED)

    def test_denied_attach_is_never_elevated_silently(self) -> None:
        mouse, driver = self.make()
        driver.attach_error = "error: Access is denied."
        mouse.start()
        self.settle(mouse)
        self.assertEqual(mouse.state, usbip_mouse.SERVING)
        self.assertEqual(mouse.problem, usbip_client.DENIED)
        self.assertIn("console as administrator", mouse.message)
        self.assertIn("attach -r 127.0.0.1 -b 1-1", mouse.message)
        self.assertEqual(self.elevated, [])                 # nothing happened by itself
        self.assertTrue(mouse.can_elevate())
        # On request, and only then, the UAC prompt is used.
        driver.attach_error = ""
        self.assertTrue(mouse.attach_elevated())
        self.assertEqual(len(self.elevated), 1)
        self.assertEqual(self.elevated[0][0], TOOL)
        driver(self.elevated[0], 1.0)                       # what Windows would then run
        self.settle(mouse)
        self.assertEqual(mouse.state, usbip_mouse.ATTACHED)
        self.assertEqual(mouse.port, 3)

    def test_elevation_is_not_offered_for_a_tool_in_a_user_folder(self) -> None:
        mouse, driver = self.make(tool="C:\\Users\\someone\\Downloads\\usbip.exe")
        driver.attach_error = "error: Access is denied."
        mouse.start()
        self.settle(mouse)
        self.assertFalse(mouse.can_elevate())
        self.assertFalse(mouse.attach_elevated())
        self.assertEqual(self.elevated, [])

    def test_port_busy(self) -> None:
        blocker = socket.socket()
        blocker.bind(("127.0.0.1", 0))
        blocker.listen(1)
        self.addCleanup(blocker.close)
        driver = FakeDriver()
        mouse = usbip_mouse.VirtualMouse(
            UsbipClient(TOOL, driver), VirtualMouseServer(port=blocker.getsockname()[1]))
        self.assertFalse(mouse.start())
        self.assertEqual(mouse.state, usbip_mouse.ERROR)
        self.assertIn("in use by another program", mouse.message)
        self.assertEqual(driver.calls, [])
        mouse.close()

    def test_close_cleans_up_even_with_a_waiting_request(self) -> None:
        mouse, driver = self.make()
        mouse.start()
        self.settle(mouse)
        assert driver.client is not None
        driver.client.submit(usbip_server.DIR_IN, 1, 64)
        mouse.close()
        self.assertFalse(mouse.running)
        self.assertTrue(any(c.endswith("detach -p 3") for c in driver.commands()))
        self.assertEqual(threading.active_count() >= 1, True)

    def test_driver_missing_and_attach_again(self) -> None:
        mouse, driver = self.make()
        driver.attach_error = "error: The system cannot find the file specified."
        mouse.start()
        self.settle(mouse)
        self.assertEqual(mouse.problem, usbip_client.DRIVER_MISSING)
        driver.attach_error = ""
        mouse.attach_again()
        self.settle(mouse)
        self.assertEqual(mouse.state, usbip_mouse.ATTACHED)
        self.assertEqual(mouse.problem, "")

    def test_state_texts(self) -> None:
        for state in (usbip_mouse.STOPPED, usbip_mouse.SERVING, usbip_mouse.ATTACHED,
                      usbip_mouse.IN_USE, usbip_mouse.ERROR):
            self.assertTrue(usbip_mouse.STATE_TEXT[state])


if __name__ == "__main__":
    unittest.main()
