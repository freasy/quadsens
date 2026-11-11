"""Tests of the driver setup: state detection, prerequisites and the installer script.

The script is run for real, but never as root and never against the system: it
gets a fake directory tree (QUADSENS_TEST_ROOT) and stand-in tools that record
their calls (tests/stub_tool.sh). No dkms, git, pkexec or module tool of the
machine is started.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from quadsens import driver_setup as ds
from quadsens.driver_setup import Kind, Probe

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "helper" / "quadsens-driver.sh"
STUB = Path(__file__).parent / "stub_tool.sh"
KERNEL = platform.release()
FORK = "0.9.5+dirscale1"
COMMIT = "2a3e21956ef5a2ddfc4523a7ad4ef8117f6f4913"
STUB_NAMES = ("dkms", "git", "make", "modprobe", "rmmod", "getent", "systemctl",
              "groupadd", "usermod", "pacman", "bash", "cc", "c++")


class FakeSystem:
    """A directory tree and tool stand-ins that describe one machine."""

    def __init__(self, test: unittest.TestCase) -> None:
        tmp = tempfile.TemporaryDirectory()
        test.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name) / "root"
        self.bin = Path(tmp.name) / "bin"
        self.log = Path(tmp.name) / "calls.log"
        self.bin.mkdir()
        for name in STUB_NAMES:
            shutil.copy(STUB, self.bin / name)
        for folder in ("usr/src", "usr/local/src", "usr/bin", "etc", "tmp",
                       "run/systemd/system", f"lib/modules/{KERNEL}/build"):
            (self.root / folder).mkdir(parents=True)
        (self.root / "etc/os-release").write_text('ID=manjaro\nID_LIKE="arch"\n')
        self.dkms: list[str] = []

    # ---- describing the machine ------------------------------------------------

    def dkms_line(self, version: str, installed: bool = True) -> None:
        self.dkms.append(f"yeetmouse-driver/{version}, {KERNEL}, x86_64: installed"
                         if installed else f"yeetmouse-driver/{version}: added")
        (self.root / "dkms-status").write_text(
            "nvidia/615.71, %s, x86_64: installed\n" % KERNEL + "\n".join(self.dkms) + "\n")

    def source(self, version: str, where: str = "usr/src") -> Path:
        folder = self.root / where / f"yeetmouse-driver-{version}"
        folder.mkdir(parents=True)
        (folder / "dkms.conf").write_text("PACKAGE_NAME=yeetmouse\n")
        return folder

    def stock(self, version: str = "0.9.5") -> None:
        self.dkms_line(version)
        self.source(version)

    def fork(self, version: str = FORK) -> None:
        self.dkms_line(version)
        self.source(version)

    def userspace(self, fork_tool: bool = True) -> None:
        tool = self.root / "usr/bin/yeetmousectl"
        tool.write_text("tool dirScaleXPos" if fork_tool else "tool")
        tool.chmod(0o755)
        unit = self.root / "usr/lib/systemd/system/yeetmouse.service"
        unit.parent.mkdir(parents=True, exist_ok=True)
        unit.write_text("unit")
        (self.root / "etc/yeetmouse.conf").write_text("sens=2\n")

    def group(self, members: str = "tester") -> None:
        (self.root / "group").write_text(f"yeetmouse:x:931:{members}\n")

    def loaded(self, fork: bool = True) -> None:
        params = self.root / "sys/module/yeetmouse/parameters"
        params.mkdir(parents=True)
        (params / "Sensitivity").write_text("1\n")
        if fork:
            (params / "DirScaleXPos").write_text("1\n")

    def packaged(self, *paths: str) -> None:
        (self.root / "packaged").write_text(
            "".join(f"{self.root}/{path}\n" for path in paths))

    # ---- looking and running ---------------------------------------------------------

    def probe(self, in_session: bool = True) -> Probe:
        def run(argv: list[str]) -> tuple[int, str]:
            env = {"PATH": f"{self.bin}:/usr/bin:/bin", "QUADSENS_TEST_ROOT": str(self.root),
                   "QUADSENS_STUB_LOG": str(self.log)}
            result = subprocess.run(argv, capture_output=True, text=True, env=env, check=False)
            return result.returncode, result.stdout

        def which(name: str) -> str | None:
            path = self.bin / name
            return str(path) if path.exists() else None

        return Probe(root=self.root, run=run, which=which, kernel=KERNEL,
                     session_groups=lambda: {931} if in_session else {1000},
                     user=lambda: "tester")

    def run(self, *args: str, fail: str = "", head: str = "", version: str = "",
            uid: str = "1000") -> subprocess.CompletedProcess[str]:
        """Feed the script to sh on standard input, as QuadSens does."""
        env = {"PATH": "/usr/bin:/bin", "QUADSENS_TEST_ROOT": str(self.root),
               "QUADSENS_TEST_PATH": str(self.bin), "QUADSENS_STUB_LOG": str(self.log),
               "QUADSENS_STUB_FAIL": fail, "QUADSENS_STUB_HEAD": head, "PKEXEC_UID": uid}
        if version:
            env["QUADSENS_STUB_VERSION"] = version
        return subprocess.run(["/bin/sh", "-s", "--", *args], input=SCRIPT.read_text(),
                              capture_output=True, text=True, env=env, check=False, timeout=60)

    def calls(self) -> list[str]:
        """The recorded calls, with the temp paths shortened."""
        if not self.log.exists():
            return []
        lines = self.log.read_text().splitlines()
        short = []
        for line in lines:
            line = line.replace(str(self.root), "")
            if "/tmp/quadsens-driver." in line:
                head, _sep, tail = line.partition("/tmp/quadsens-driver.")
                line = head + "WORK" + tail[8:]
            short.append(line)
        return short

    def exists(self, path: str) -> bool:
        return (self.root / path).exists()

    def state(self) -> dict[str, str]:
        try:
            text = (self.root / "var/lib/quadsens/driver-state").read_text()
        except OSError:
            return {}
        return dict(line.split("=", 1) for line in text.splitlines())


INSTALL_BUILD = [
    "git init -q WORK/src",
    f"git -C WORK/src fetch -q --depth 1 https://github.com/freasy/YeetMouse.git {COMMIT}",
    "git -C WORK/src checkout -q --detach FETCH_HEAD",
    "make -C WORK/src yeetmousectl",
]
RELOAD = ["modprobe -r yeetmouse", "modprobe yeetmouse", "systemctl daemon-reload",
          "systemctl enable yeetmouse.service", "systemctl restart yeetmouse.service"]
USERSPACE = ["make -C WORK/src install_userspace install_config install_service DESTDIR=/"]


class DetectionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.sys = FakeSystem(self)
        self.pin = ds.pin()

    def detect(self, **kwargs: bool) -> ds.SetupState:
        return ds.detect(self.sys.probe(**kwargs), self.pin)

    def test_pin_comes_from_the_script(self) -> None:
        self.assertEqual(self.pin, ds.Pin("https://github.com/freasy/YeetMouse.git", COMMIT,
                                          FORK))
        self.assertEqual(len(self.pin.commit), 40)
        with self.assertRaises(ds.SetupError):
            ds.pin('FORK_URL="x"\nFORK_COMMIT="2a3e219"\nFORK_VERSION="1"\n')
        with self.assertRaises(ds.SetupError):
            ds.pin("nothing here")

    def test_nothing_installed(self) -> None:
        state = self.detect()
        self.assertIs(state.kind, Kind.NONE)
        self.assertEqual(state.versions, ())
        self.assertFalse(state.loaded or state.tool or state.group_exists or state.service)
        self.assertFalse(state.can_revert)
        texts = [text for _ok, text in state.lines(self.pin)]
        self.assertIn("Driver: no yeetmouse driver is installed through DKMS.", texts)
        self.assertIn("Module: not loaded.", texts)
        self.assertIn("yeetmousectl: not installed.", texts)

    def test_stock_from_the_upstream_installer(self) -> None:
        self.sys.stock()
        self.sys.userspace(fork_tool=False)
        self.sys.group()
        self.sys.loaded(fork=False)
        state = self.detect()
        self.assertIs(state.kind, Kind.STOCK)
        self.assertEqual((state.stock_version, state.package), ("0.9.5", ""))
        self.assertTrue(state.loaded)
        self.assertFalse(state.loaded_is_fork)
        self.assertEqual(state.tool, "/usr/bin/yeetmousectl")
        self.assertFalse(state.tool_is_fork)
        self.assertTrue(state.group_exists and state.in_group and state.group_active)
        texts = " ".join(text for _ok, text in state.lines(self.pin))
        self.assertIn("unmodified driver 0.9.5", texts)
        self.assertIn("without the directional parameters", texts)
        self.assertIn("does not save the directional values", texts)

    def test_stock_from_a_distribution_package(self) -> None:
        self.sys.stock()
        self.sys.packaged("usr/src/yeetmouse-driver-0.9.5")
        state = self.detect()
        self.assertIs(state.kind, Kind.STOCK_PACKAGED)
        self.assertEqual(state.package, "yeetmouse-driver-dkms")
        self.assertIn("Remove that package first", state.lines(self.pin)[0][1])

    def test_fork_in_the_pinned_version(self) -> None:
        self.sys.fork()
        self.sys.userspace()
        self.sys.group()
        self.sys.loaded()
        state = self.detect()
        self.assertIs(state.kind, Kind.FORK)
        self.assertEqual(state.fork_version, FORK)
        self.assertTrue(state.loaded_is_fork and state.tool_is_fork and state.service)
        self.assertEqual(state.backup_version, "")
        self.assertTrue(state.can_revert)
        self.assertTrue(all(ok for ok, _text in state.lines(self.pin) if ok is not None))

    def test_fork_in_another_version(self) -> None:
        self.sys.fork("0.9.6+dirscale2")
        state = self.detect()
        self.assertIs(state.kind, Kind.FORK_OTHER)
        self.assertEqual(state.fork_version, "0.9.6+dirscale2")
        self.assertIn("pinned to 0.9.5+dirscale1", state.lines(self.pin)[0][1])

    def test_fork_added_but_not_built_for_this_kernel(self) -> None:
        self.sys.dkms_line(FORK, installed=False)
        self.assertIs(self.detect().kind, Kind.FORK_OTHER)

    def test_hand_installed_fork_with_stock_moved_aside(self) -> None:
        # This is how the first machine was set up by hand: no state file.
        self.sys.fork()
        self.sys.userspace()
        self.sys.group()
        self.sys.loaded()
        self.sys.source("0.9.5", "usr/local/src")
        state = self.detect(in_session=False)
        self.assertIs(state.kind, Kind.FORK)
        self.assertEqual(state.backup_version, "0.9.5")
        self.assertFalse(state.recorded)
        self.assertTrue(state.in_group)
        self.assertFalse(state.group_active)
        texts = [text for _ok, text in state.lines(self.pin)]
        self.assertIn("Previous driver kept for Revert: 0.9.5 (in /usr/local/src).", texts)
        self.assertTrue(any("this session is not yet" in text for text in texts))

    def test_recorded_backup_wins_over_guessing(self) -> None:
        self.sys.fork()
        self.sys.source("0.9.5", "usr/local/src")
        self.sys.source("0.9.4", "usr/local/src")
        self.assertEqual(self.detect().backup_version, "")      # two candidates: no guess
        state_dir = self.sys.root / "var/lib/quadsens"
        state_dir.mkdir(parents=True)
        (state_dir / "driver-state").write_text("STOCK_VERSION=0.9.4\nCREATED_CTL=1\n")
        state = self.detect()
        self.assertEqual(state.backup_version, "0.9.4")
        self.assertTrue(state.recorded)

    def test_dkms_status_formats(self) -> None:
        old = "yeetmouse-driver, 0.9.5, %s, x86_64: installed\n" % KERNEL
        self.assertEqual(ds.parse_dkms_status(old, KERNEL), [("0.9.5", True)])
        other_kernel = "yeetmouse-driver/0.9.5, 5.0.0-other, x86_64: installed\n"
        self.assertEqual(ds.parse_dkms_status(other_kernel, KERNEL), [("0.9.5", False)])
        self.assertEqual(ds.parse_dkms_status("nvidia/1, k, x: installed\n", KERNEL), [])
        self.assertEqual(ds.parse_dkms_status("yeetmouse-driver/../x: added\n", KERNEL), [])


class PrerequisiteTest(unittest.TestCase):
    def setUp(self) -> None:
        self.sys = FakeSystem(self)

    def test_everything_present(self) -> None:
        (self.sys.bin / "pkexec").write_text("")
        checks = ds.prerequisites(self.sys.probe())
        self.assertEqual([check.name for check in checks], [
            "DKMS", "git", "make", "C compiler", "C++ compiler",
            "bash (for the fork's Makefile)", "Kernel headers", "pkexec (polkit)", "systemd"])
        self.assertTrue(all(check.ok for check in checks))
        self.assertTrue(ds.ready(checks))
        self.assertTrue(all(check.text().startswith("✓ ") for check in checks))

    def test_missing_pieces_come_with_a_hint_for_the_distribution(self) -> None:
        for name in ("dkms", "c++"):
            (self.sys.bin / name).unlink()
        shutil.rmtree(self.sys.root / "lib/modules")
        shutil.rmtree(self.sys.root / "run/systemd")
        hints = {
            'ID=manjaro\nID_LIKE="arch"\n': ("sudo pacman -S dkms", "sudo pacman -S gcc",
                                              "linuxXY-headers"),
            "ID=ubuntu\nID_LIKE=debian\n": ("sudo apt install dkms", "sudo apt install g++",
                                            "linux-headers-$(uname -r)"),
            "ID=fedora\n": ("sudo dnf install dkms", "sudo dnf install gcc-c++",
                            "kernel-devel"),
        }
        for release, (dkms, compiler, headers) in hints.items():
            (self.sys.root / "etc/os-release").write_text(release)
            checks = {check.name: check for check in ds.prerequisites(self.sys.probe())}
            self.assertFalse(ds.ready(list(checks.values())))
            self.assertIn(dkms, checks["DKMS"].text())
            self.assertTrue(checks["DKMS"].text().startswith("✕ DKMS: not installed."))
            self.assertIn(compiler, checks["C++ compiler"].text())
            self.assertIn(headers, checks["Kernel headers"].text())
            self.assertIn(KERNEL, checks["Kernel headers"].detail)
            self.assertFalse(checks["pkexec (polkit)"].ok)
            self.assertFalse(checks["systemd"].ok)
            self.assertFalse(checks["systemd"].required)       # a remark, not a blocker
            self.assertTrue(checks["systemd"].text().startswith("– "))
        (self.sys.root / "etc/os-release").write_text("ID=void\n")
        self.assertEqual(ds.distro_family(self.sys.probe()), "")
        check = ds.prerequisites(self.sys.probe())[0]
        self.assertIn("your package manager", check.text())

    def test_only_required_checks_block(self) -> None:
        checks = [ds.Check("a", True, ""), ds.Check("b", False, "", required=False)]
        self.assertTrue(ds.ready(checks))
        self.assertFalse(ds.ready([*checks, ds.Check("c", False, "")]))


class LaunchTest(unittest.TestCase):
    def test_script_goes_to_the_shell_on_standard_input(self) -> None:
        text = ds.script_text()
        self.assertEqual(text, SCRIPT.read_text())
        launch = ds.launch(ds.INSTALL, pkexec="true")     # "true" stands in for pkexec
        self.assertEqual(Path(launch.argv[0]).name, "true")
        self.assertTrue(os.path.isabs(launch.argv[0]))
        self.assertEqual(launch.argv[1:], ["--disable-internal-agent", "/bin/sh", "-s", "--",
                                           "install"])
        self.assertEqual(launch.stdin, text)              # exactly what is shown
        self.assertEqual(ds.launch(ds.REVERT, pkexec="true").argv[-1], "revert")
        # No file from the checkout, the bundle or a temp directory is ever executed.
        self.assertFalse(any("quadsens-driver" in part for part in launch.argv))

    def test_dry_run_needs_no_root(self) -> None:
        launch = ds.launch(ds.REVERT, dry_run=True)
        self.assertEqual(launch.argv, ["/bin/sh", "-s", "--", "--dry-run", "revert"])
        self.assertTrue(launch.stdin)

    def test_errors(self) -> None:
        with self.assertRaises(ds.SetupError):
            ds.launch("purge", pkexec="true")
        with self.assertRaisesRegex(ds.SetupError, "pkexec is not installed"):
            ds.launch(ds.INSTALL, pkexec="no-such-pkexec")
        with self.assertRaisesRegex(ds.SetupError, "missing, not root-owned or differs"):
            ds.launch(ds.INSTALL, pkexec="true", use_installed=True)

    def test_installed_copy_must_be_root_owned_and_identical(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp) / "quadsens-driver.sh"
            copy.write_text(ds.script_text())
            copy.chmod(0o755)
            if os.geteuid() != 0:
                self.assertFalse(ds.installed_script_ok(ds.script_text(), copy))

    def test_changes_are_spelled_out(self) -> None:
        pinned = ds.pin()
        stock = ds.SetupState(Kind.STOCK, stock_version="0.9.5")
        install = " ".join(ds.changes(ds.INSTALL, stock, pinned))
        for expected in ("at commit 2a3e21956ef5", "exactly that commit", "as root",
                         "moves its source from /usr/src to /usr/local/src",
                         "existing configuration is kept", "adds you to it",
                         "Mouse acceleration is off for a moment"):
            self.assertIn(expected, install)
        self.assertNotIn("moves its source", " ".join(
            ds.changes(ds.INSTALL, ds.SetupState(Kind.NONE), pinned)))
        kept = ds.SetupState(Kind.FORK, fork_version=FORK, backup_version="0.9.5")
        self.assertIn("Moves the previous driver 0.9.5 back", " ".join(
            ds.changes(ds.REVERT, kept, pinned)))
        bare = " ".join(ds.changes(ds.REVERT, ds.SetupState(Kind.FORK, fork_version=FORK),
                                   pinned))
        self.assertIn("no yeetmouse module is installed", bare)
        self.assertIn("Keeps /etc/yeetmouse.conf", bare)


class ScriptTest(unittest.TestCase):
    def setUp(self) -> None:
        self.sys = FakeSystem(self)

    def test_shell_syntax_and_no_bashisms(self) -> None:
        text = SCRIPT.read_text()
        self.assertTrue(text.startswith("#!/bin/sh\n"))
        self.assertIn("set -eu\n", text)
        self.assertTrue(text.rstrip().endswith('main "$@" </dev/null; exit $?'))
        for bashism in ("local ", "function ", "<<<", "$'", "${BASH", "pipefail", "eval ",
                        "\nsource ", "    source ", "if [[", "&& [[", "|| [["):
            self.assertNotIn(bashism, text, bashism)
        check = subprocess.run(["/bin/sh", "-n", str(SCRIPT)], capture_output=True, text=True)
        self.assertEqual((check.returncode, check.stderr), (0, ""))
        shellcheck = shutil.which("shellcheck")
        if shellcheck:
            result = subprocess.run([shellcheck, "-s", "sh", "-S", "warning", str(SCRIPT),
                                     str(STUB)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout)

    def test_usage_errors_change_nothing(self) -> None:
        for args in ((), ("purge",), ("install", "revert"), ("--force", "install")):
            result = self.sys.run(*args)
            self.assertEqual(result.returncode, 2, args)
            self.assertIn("usage:", result.stderr)
        self.assertEqual(self.sys.calls(), [])

    def test_install_on_a_clean_system(self) -> None:
        result = self.sys.run("install")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.sys.calls(), [
            *INSTALL_BUILD,
            "groupadd -r yeetmouse",
            "usermod -aG yeetmouse tester",
            "make -C WORK/src setup_dkms DESTDIR=/",
            f"dkms add yeetmouse-driver/{FORK}",
            f"dkms build yeetmouse-driver/{FORK}",
            f"dkms install yeetmouse-driver/{FORK}",
            *USERSPACE, *RELOAD])
        steps = [line for line in result.stdout.splitlines() if line.startswith("[")]
        self.assertEqual([line.split("]")[0] for line in steps],
                         ["[%d/9" % n for n in range(1, 10)])
        self.assertIn(f"verified: commit {COMMIT}", result.stdout)
        self.assertIn("DONE. The yeetmouse fork", result.stdout)
        self.assertIn("Log out and in again", result.stdout)
        self.assertEqual(self.sys.state(), {
            "CREATED_CTL": "1", "CREATED_SERVICE": "1", "CREATED_SYSUSERS": "1",
            "CREATED_CONFIG": "1", "FORK_COMMIT": COMMIT})
        self.assertEqual(list((self.sys.root / "tmp").iterdir()), [])   # work dir removed

    def test_install_over_stock_moves_it_aside(self) -> None:
        self.sys.stock()
        self.sys.userspace(fork_tool=False)
        self.sys.group()
        result = self.sys.run("install")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.sys.calls(), [
            *INSTALL_BUILD,
            "make -C WORK/src setup_dkms DESTDIR=/",
            f"dkms add yeetmouse-driver/{FORK}",
            f"dkms build yeetmouse-driver/{FORK}",       # built before stock is touched
            "dkms remove yeetmouse-driver/0.9.5 --all",
            f"dkms install yeetmouse-driver/{FORK}",
            *USERSPACE, *RELOAD])
        self.assertFalse(self.sys.exists("usr/src/yeetmouse-driver-0.9.5"))
        self.assertTrue(self.sys.exists("usr/local/src/yeetmouse-driver-0.9.5/dkms.conf"))
        self.assertEqual(self.sys.state(), {"STOCK_VERSION": "0.9.5", "CREATED_SYSUSERS": "1",
                                            "FORK_COMMIT": COMMIT})
        self.assertEqual((self.sys.root / "etc/yeetmouse.conf").read_text(), "sens=2\n")
        self.assertIn("keeping the existing", result.stdout)
        self.assertIn('"revert" puts it back', result.stdout)

    def test_install_refuses_a_packaged_driver(self) -> None:
        self.sys.stock()
        self.sys.packaged("usr/src/yeetmouse-driver-0.9.5")
        result = self.sys.run("install")
        self.assertEqual(result.returncode, 1)
        self.assertIn('belongs to the package "yeetmouse-driver-dkms"', result.stderr)
        self.assertIn("Remove that package", result.stderr)
        self.assertEqual(self.sys.calls(), [])
        self.assertIn("State: nothing was changed", result.stdout)
        self.assertTrue(self.sys.exists("usr/src/yeetmouse-driver-0.9.5"))

    def test_install_refuses_a_packaged_tool(self) -> None:
        self.sys.userspace(fork_tool=False)
        self.sys.packaged("usr/bin/yeetmousectl")
        result = self.sys.run("install")
        self.assertEqual(result.returncode, 1)
        self.assertIn("/usr/bin/yeetmousectl belongs to the package", result.stderr)
        self.assertEqual(self.sys.calls(), [])

    def test_install_is_a_no_op_when_everything_is_in_place(self) -> None:
        self.sys.fork()
        self.sys.userspace()
        self.sys.group()
        self.sys.source("0.9.5", "usr/local/src")
        result = self.sys.run("install")
        self.assertEqual(result.returncode, 0)
        self.assertIn("Nothing to do", result.stdout)
        self.assertEqual(self.sys.calls(), [])

    def test_install_repairs_missing_userspace(self) -> None:
        self.sys.fork()
        self.sys.group("someone-else")
        result = self.sys.run("install")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.sys.calls(), [
            *INSTALL_BUILD, "usermod -aG yeetmouse tester", *USERSPACE, *RELOAD])
        self.assertIn("already built and installed", result.stdout)

    def test_commit_mismatch_aborts_before_anything_is_built(self) -> None:
        self.sys.stock()
        result = self.sys.run("install", head="f" * 40)
        self.assertEqual(result.returncode, 1)
        self.assertIn("not the pinned " + COMMIT, result.stderr)
        self.assertIn("Nothing was installed", result.stderr)
        self.assertEqual(self.sys.calls(), INSTALL_BUILD[:3])   # fetched, nothing more
        self.assertTrue(self.sys.exists("usr/src/yeetmouse-driver-0.9.5"))
        self.assertIn("State: nothing was changed", result.stdout)
        self.assertEqual(list((self.sys.root / "tmp").iterdir()), [])

    def test_wrong_version_in_the_source_aborts(self) -> None:
        result = self.sys.run("install", version="9.9.9")
        self.assertEqual(result.returncode, 1)
        self.assertIn('says version "9.9.9"', result.stderr)
        self.assertEqual(self.sys.calls(), INSTALL_BUILD[:3])

    def test_build_failure_leaves_the_stock_driver_alone(self) -> None:
        self.sys.stock()
        self.sys.group()
        result = self.sys.run("install", fail="dkms build")
        self.assertEqual(result.returncode, 9)
        calls = self.sys.calls()
        self.assertNotIn("dkms remove yeetmouse-driver/0.9.5 --all", calls)
        self.assertIn(f"dkms remove yeetmouse-driver/{FORK} --all", calls)   # tidy up
        self.assertTrue(self.sys.exists("usr/src/yeetmouse-driver-0.9.5"))
        self.assertFalse(self.sys.exists(f"usr/src/yeetmouse-driver-{FORK}"))
        self.assertIn("FAILED", result.stdout)
        self.assertIn("there was no previous driver", result.stdout)

    def test_failure_after_the_stock_was_removed_restores_it(self) -> None:
        self.sys.stock()
        self.sys.group()
        result = self.sys.run("install", fail=f"dkms install yeetmouse-driver/{FORK}")
        self.assertEqual(result.returncode, 9)
        calls = self.sys.calls()
        tail = calls[calls.index("dkms remove yeetmouse-driver/0.9.5 --all"):]
        self.assertEqual(tail, [
            "dkms remove yeetmouse-driver/0.9.5 --all",
            f"dkms install yeetmouse-driver/{FORK}",               # fails
            f"dkms remove yeetmouse-driver/{FORK} --all",          # rollback from here
            "dkms install yeetmouse-driver/0.9.5",
            "modprobe yeetmouse"])
        self.assertTrue(self.sys.exists("usr/src/yeetmouse-driver-0.9.5/dkms.conf"))
        self.assertFalse(self.sys.exists("usr/local/src/yeetmouse-driver-0.9.5"))
        self.assertFalse(self.sys.exists(f"usr/src/yeetmouse-driver-{FORK}"))
        self.assertEqual(self.sys.state(), {})
        self.assertIn("State: the previous driver 0.9.5 is installed again.", result.stdout)

    def test_failed_rollback_says_that_no_module_is_installed(self) -> None:
        self.sys.stock()
        self.sys.group()
        result = self.sys.run("install", fail="dkms install")   # both installs fail
        self.assertEqual(result.returncode, 9)
        self.assertIn("State: NO yeetmouse module is installed", result.stdout)
        self.assertIn("dkms install yeetmouse-driver/0.9.5", result.stdout)

    def test_occupied_backup_location_stops_early(self) -> None:
        self.sys.stock()
        self.sys.source("0.9.5", "usr/local/src")
        result = self.sys.run("install")
        self.assertEqual(result.returncode, 1)
        self.assertIn("exists already", result.stderr)
        self.assertEqual(self.sys.calls(), [])

    def test_revert_restores_the_recorded_stock(self) -> None:
        self.sys.stock()
        self.sys.userspace(fork_tool=False)
        self.sys.group()
        self.assertEqual(self.sys.run("install").returncode, 0)
        self.sys.dkms.clear()
        self.sys.dkms_line(FORK)          # what DKMS reports after the install
        self.sys.log.unlink()
        result = self.sys.run("revert")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.sys.calls(), [
            f"dkms remove yeetmouse-driver/{FORK} --all",
            "dkms install yeetmouse-driver/0.9.5",
            *RELOAD])
        self.assertTrue(self.sys.exists("usr/src/yeetmouse-driver-0.9.5/dkms.conf"))
        self.assertFalse(self.sys.exists("usr/local/src/yeetmouse-driver-0.9.5"))
        self.assertFalse(self.sys.exists(f"usr/src/yeetmouse-driver-{FORK}"))
        self.assertTrue(self.sys.exists("usr/bin/yeetmousectl"))   # the tool stays
        self.assertEqual(self.sys.state(), {})
        self.assertIn("DONE. The previous driver 0.9.5 is installed again.", result.stdout)

    def test_revert_of_the_hand_installed_state(self) -> None:
        self.sys.fork()
        self.sys.userspace()
        self.sys.group()
        self.sys.source("0.9.5", "usr/local/src")     # moved by hand, no state file
        result = self.sys.run("revert")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.sys.calls(), [
            f"dkms remove yeetmouse-driver/{FORK} --all",
            "dkms install yeetmouse-driver/0.9.5",
            *RELOAD])
        self.assertTrue(self.sys.exists("usr/src/yeetmouse-driver-0.9.5"))
        self.assertTrue(self.sys.exists("usr/bin/yeetmousectl"))
        self.assertTrue(self.sys.exists("etc/yeetmouse.conf"))

    def test_revert_without_a_stock_removes_what_install_added(self) -> None:
        self.assertEqual(self.sys.run("install").returncode, 0)
        self.sys.dkms_line(FORK)
        self.sys.group()
        self.sys.log.unlink()
        result = self.sys.run("revert")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.sys.calls(), [
            f"dkms remove yeetmouse-driver/{FORK} --all",
            "modprobe -r yeetmouse",
            "systemctl disable --now yeetmouse.service",
            "systemctl daemon-reload"])
        for gone in (f"usr/src/yeetmouse-driver-{FORK}", "usr/bin/yeetmousectl",
                     "usr/lib/systemd/system/yeetmouse.service",
                     "usr/lib/sysusers.d/yeetmouse.conf", "var/lib/quadsens/driver-state"):
            self.assertFalse(self.sys.exists(gone), gone)
        self.assertTrue(self.sys.exists("etc/yeetmouse.conf"))     # the config is kept
        self.assertIn("No yeetmouse module is installed now.", result.stdout)

    def test_revert_keeps_files_it_did_not_add(self) -> None:
        self.sys.fork()
        self.sys.userspace()       # there before, no state file, no stock
        result = self.sys.run("revert")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(self.sys.exists("usr/bin/yeetmousectl"))
        self.assertTrue(self.sys.exists("usr/lib/systemd/system/yeetmouse.service"))

    def test_revert_with_nothing_installed(self) -> None:
        result = self.sys.run("revert")
        self.assertEqual(result.returncode, 0)
        self.assertIn("Nothing to do", result.stdout)
        self.assertEqual(self.sys.calls(), [])

    def test_revert_stops_when_the_kept_driver_cannot_be_moved_back(self) -> None:
        self.sys.fork()
        self.sys.source("0.9.5", "usr/local/src")
        self.sys.source("0.9.5")              # something is in the way
        result = self.sys.run("revert")
        self.assertEqual(result.returncode, 1)
        self.assertIn("exists already", result.stderr)
        self.assertEqual(self.sys.calls(), [])   # stopped before removing anything

    def test_revert_failure_names_the_state(self) -> None:
        self.sys.fork()
        self.sys.source("0.9.5", "usr/local/src")
        result = self.sys.run("revert", fail="dkms install")
        self.assertEqual(result.returncode, 9)
        self.assertIn("State: NO yeetmouse module is installed", result.stdout)
        self.assertIn("dkms install yeetmouse-driver/0.9.5", result.stdout)

    def test_dry_run_prints_the_plan_and_touches_nothing(self) -> None:
        self.sys.stock()
        self.sys.group("someone-else")
        before = sorted(str(p) for p in self.sys.root.rglob("*"))
        result = self.sys.run("--dry-run", "install")
        self.assertEqual(result.returncode, 0, result.stdout[-600:] + result.stderr)
        self.assertEqual(self.sys.calls(), [])
        self.assertEqual(sorted(str(p) for p in self.sys.root.rglob("*")), before)
        out = result.stdout
        self.assertIn("Dry run: nothing is changed.", out)
        for expected in (
                f"would run: git fetch --depth 1 https://github.com/freasy/YeetMouse.git {COMMIT}",
                "usermod -aG yeetmouse tester",
                "dkms remove yeetmouse-driver/0.9.5 --all",
                f"dkms install yeetmouse-driver/{FORK}",
                "would record: STOCK_VERSION=0.9.5",
                "modprobe yeetmouse"):
            self.assertIn(expected, out)
        self.assertEqual(len([l for l in out.splitlines() if l.startswith("[")]), 9)
        self.sys.dkms.clear()
        self.sys.fork()
        shutil.move(str(self.sys.root / "usr/src/yeetmouse-driver-0.9.5"),
                    str(self.sys.root / "usr/local/src"))
        revert = self.sys.run("--dry-run", "revert")
        self.assertEqual(revert.returncode, 0, revert.stderr)
        self.assertIn(f"would run: {self.sys.bin}/dkms remove yeetmouse-driver/{FORK} --all",
                      revert.stdout)
        self.assertIn("dkms install yeetmouse-driver/0.9.5", revert.stdout)
        self.assertEqual(self.sys.calls(), [])

    def test_invoking_user_comes_from_pkexec_uid_only(self) -> None:
        for uid in ("", "abc", "0", "12; rm -rf /"):
            system = FakeSystem(self)
            result = system.run("install", uid=uid)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(any(call.startswith("usermod") for call in system.calls()), uid)
            self.assertIn("could not tell who started this", result.stdout)

    def test_environment_of_the_caller_is_not_passed_on(self) -> None:
        (self.sys.bin / "make").write_text(
            '#!/bin/sh\necho "make $* LD=${LD_PRELOAD:-} X=${EVIL:-}" >> "$QUADSENS_STUB_LOG"\n'
            'exec "$(dirname "$0")/git" __noop__\n')
        env = {"PATH": "/usr/bin:/bin", "QUADSENS_TEST_ROOT": str(self.sys.root),
               "QUADSENS_TEST_PATH": str(self.sys.bin), "QUADSENS_STUB_LOG": str(self.sys.log),
               "LD_PRELOAD": "", "EVIL": "yes", "PKEXEC_UID": "1000"}
        subprocess.run(["/bin/sh", "-s", "--", "install"], input=SCRIPT.read_text(),
                       capture_output=True, text=True, env=env, check=False)
        makes = [line for line in self.sys.log.read_text().splitlines()
                 if line.startswith("make ") and "X=" in line]
        self.assertTrue(makes)
        self.assertTrue(all(line.endswith("X=") for line in makes), makes)

    def test_test_hook_cannot_be_used_in_a_real_run(self) -> None:
        text = SCRIPT.read_text()
        hook = text[text.index("# ---- test hook"):text.index("# ---- arguments")]
        self.assertIn('if [ "$(id -u)" -eq 0 ]; then', hook)
        self.assertIn("QUADSENS_TEST_ROOT is not allowed as root", hook)
        self.assertLess(hook.index("id -u"), hook.index('ROOT="$QUADSENS_TEST_ROOT"'))
        # Without the hook and without root the script refuses to act at all.
        result = subprocess.run(["/bin/sh", "-s", "--", "install"], input=text,
                                capture_output=True, text=True,
                                env={"PATH": "/usr/bin:/bin"}, check=False)
        if os.geteuid() != 0:
            self.assertEqual(result.returncode, 1)
            self.assertIn("has to run as root", result.stderr)
        relative = subprocess.run(["/bin/sh", "-s", "--", "install"], input=text,
                                  capture_output=True, text=True, check=False,
                                  env={"PATH": "/usr/bin:/bin", "QUADSENS_TEST_ROOT": "rel"})
        self.assertEqual(relative.returncode, 2)

    def test_script_is_bundled_with_the_binary(self) -> None:
        build = (ROOT / "build.sh").read_text()
        self.assertIn("--add-data helper/quadsens-driver.sh:helper", build)
        self.assertEqual(ds.SCRIPT_FILE, "helper/quadsens-driver.sh")


if __name__ == "__main__":
    unittest.main()
