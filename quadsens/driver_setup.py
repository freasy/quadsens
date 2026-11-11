"""Find out what yeetmouse driver is installed and what installing the fork needs (no Qt).

The actual work is done by the shell script helper/quadsens-driver.sh, which
QuadSens feeds to ``pkexec /bin/sh -s`` on request. This module only looks:
it reads files and asks DKMS and the package manager, and it builds the
command line. Paths and the command runner can be replaced, which is how the
tests describe other machines.
"""

from __future__ import annotations

import enum
import os
import re
import shutil
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from .persist import is_trusted_tool
from .resources import resource_path

SCRIPT_FILE = "helper/quadsens-driver.sh"
DKMS_NAME = "yeetmouse-driver"
MODULE = "yeetmouse"
GROUP = "yeetmouse"
DIRSCALE_PARAM = "DirScaleXPos"
TOOL_MARKER = b"dirScaleXPos"   # only the fork's yeetmousectl knows this key
SAFE_PATH = "/usr/sbin:/usr/bin:/sbin:/bin"
SHELL = "/bin/sh"
# Where the script may be installed instead of being fed through standard input.
INSTALLED_SCRIPT = Path("/usr/local/libexec/quadsens-driver.sh")
MANUAL_DOC = ("https://github.com/freasy/YeetMouse/blob/feature/directional-scalars/"
              "DIRECTIONAL_SCALING.md")
INSTALL, REVERT = "install", "revert"

Runner = Callable[[Sequence[str]], "tuple[int, str]"]


class SetupError(Exception):
    """The script is missing or damaged, or the command cannot be built."""


# ---- the script ------------------------------------------------------------------


def script_text() -> str:
    """The installer script exactly as it is shown to the user and fed to the shell."""
    path = resource_path(SCRIPT_FILE)
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SetupError(f"{path}: cannot read the driver script ({exc.strerror})") from exc


@dataclass(frozen=True)
class Pin:
    """What the script is pinned to; read from the script, the one place it is written."""

    url: str
    commit: str
    version: str


def pin(text: str | None = None) -> Pin:
    text = script_text() if text is None else text
    values = {}
    for key in ("FORK_URL", "FORK_COMMIT", "FORK_VERSION"):
        match = re.search(rf'^{key}="([^"\n]+)"$', text, re.MULTILINE)
        if match is None:
            raise SetupError(f"the driver script does not define {key}")
        values[key] = match.group(1)
    if re.fullmatch(r"[0-9a-f]{40}", values["FORK_COMMIT"]) is None:
        raise SetupError("the driver script is not pinned to a full commit hash")
    return Pin(values["FORK_URL"], values["FORK_COMMIT"], values["FORK_VERSION"])


# ---- looking at the system -----------------------------------------------------


def _run(argv: Sequence[str]) -> tuple[int, str]:
    """Run a read-only query with a fixed PATH; never raises."""
    try:
        result = subprocess.run(list(argv), capture_output=True, text=True, check=False,
                                timeout=20, env={"PATH": SAFE_PATH, "LC_ALL": "C"})
    except (OSError, subprocess.SubprocessError):
        return 127, ""
    return result.returncode, result.stdout


@dataclass
class Probe:
    """Access to the system. The defaults are the real system; tests replace them."""

    root: Path = Path("/")
    run: Runner = _run
    which: Callable[[str], str | None] = lambda name: shutil.which(name, path=SAFE_PATH)
    kernel: str = field(default_factory=lambda: os.uname().release)
    session_groups: Callable[[], set[int]] = lambda: set(os.getgroups()) | {os.getegid()}
    user: Callable[[], str] = lambda: _user_name()

    def path(self, absolute: str) -> Path:
        return self.root / absolute.lstrip("/")


def _user_name() -> str:
    import pwd
    try:
        return pwd.getpwuid(os.getuid()).pw_name
    except KeyError:
        return ""


class Kind(enum.Enum):
    NONE = "no yeetmouse driver is installed"
    STOCK = "the unmodified driver is installed"
    STOCK_PACKAGED = "the unmodified driver is installed from a distribution package"
    FORK = "the fork is installed"
    FORK_OTHER = "the fork is installed in another version than the pinned one"


@dataclass(frozen=True)
class SetupState:
    kind: Kind
    versions: tuple[tuple[str, bool], ...] = ()  # (version, installed for this kernel)
    fork_version: str = ""        # the fork version found, "" if none
    stock_version: str = ""       # another version registered with DKMS
    package: str = ""             # distribution package that owns the stock driver
    loaded: bool = False          # a yeetmouse module is loaded now
    loaded_is_fork: bool = False  # ... and it has the directional parameters
    tool: str = ""                # path of yeetmousectl, "" if missing
    tool_is_fork: bool = False
    service: bool = False
    group_exists: bool = False
    in_group: bool = False        # the user is a member according to the group database
    group_active: bool = False    # ... and this session already has the group
    backup_version: str = ""      # a previous driver kept for "revert"
    recorded: bool = False        # an install by QuadSens left its state file

    def lines(self, pinned: Pin) -> list[tuple[bool | None, str]]:
        """The state as (ok, text) lines; ok is None for plain information."""
        out: list[tuple[bool | None, str]] = []
        if self.kind is Kind.FORK:
            out.append((True, f"Driver: the fork {self.fork_version} is installed (DKMS)."))
        elif self.kind is Kind.FORK_OTHER:
            out.append((False, f"Driver: the fork is installed as {self.fork_version}, "
                               f"QuadSens is pinned to {pinned.version}."))
        elif self.kind is Kind.STOCK:
            out.append((False, f"Driver: the unmodified driver {self.stock_version} is "
                               "installed. Install moves it aside; Revert puts it back."))
        elif self.kind is Kind.STOCK_PACKAGED:
            out.append((False, f"Driver: the unmodified driver {self.stock_version} belongs "
                               f"to the package \"{self.package}\". Remove that package "
                               "first; the installer does not touch packaged files."))
        else:
            out.append((False, "Driver: no yeetmouse driver is installed through DKMS."))
        if not self.loaded:
            out.append((False, "Module: not loaded."))
        elif self.loaded_is_fork:
            out.append((True, "Module: loaded, with the directional parameters."))
        else:
            out.append((False, "Module: loaded, but without the directional parameters."))
        if not self.tool:
            out.append((False, "yeetmousectl: not installed."))
        elif self.tool_is_fork:
            out.append((True, f"yeetmousectl: {self.tool}, the fork's (saves the "
                              "directional values)."))
        else:
            out.append((False, f"yeetmousectl: {self.tool}, the unmodified one (does not "
                               "save the directional values)."))
        if not self.group_exists:
            out.append((False, f"Group {GROUP}: does not exist yet."))
        elif self.group_active:
            out.append((True, f"Group {GROUP}: your session is a member."))
        elif self.in_group:
            out.append((False, f"Group {GROUP}: you are a member, but this session is not "
                               "yet. Log out and in again."))
        else:
            out.append((False, f"Group {GROUP}: you are not a member."))
        if self.backup_version:
            out.append((None, f"Previous driver kept for Revert: {self.backup_version} "
                              "(in /usr/local/src)."))
        else:
            out.append((None, "No previous driver is kept: Revert would leave no yeetmouse "
                              "module installed."))
        return out

    @property
    def can_revert(self) -> bool:
        """Whether there is anything "revert" would undo."""
        return bool(self.fork_version) or bool(self.backup_version)


_VERSION = re.compile(r"[0-9A-Za-z.+_~-]+")


def parse_dkms_status(output: str, kernel: str) -> list[tuple[str, bool]]:
    """(version, installed for this kernel) of every registered yeetmouse driver."""
    found: dict[str, bool] = {}
    for line in output.splitlines():
        match = re.match(rf"^{re.escape(DKMS_NAME)}[/, ]+([^,: ]+)(.*)$", line.strip())
        if match is None or _VERSION.fullmatch(match.group(1)) is None:
            continue
        version, rest = match.groups()
        installed = "installed" in rest and kernel in rest
        found[version] = found.get(version, False) or installed
    return sorted(found.items())


def package_owner(probe: Probe, path: Path) -> str:
    """The distribution package that owns ``path``, "" if none or unknown."""
    for argv, pick in (
            (["pacman", "-Qqo", "--", str(path)], lambda out: out.strip().splitlines()[0]),
            (["dpkg", "-S", "--", str(path)], lambda out: out.split(":", 1)[0].strip()),
            (["rpm", "-qf", "--", str(path)], lambda out: out.strip().splitlines()[0])):
        tool = probe.which(argv[0])
        if tool is None:
            continue
        code, out = probe.run([tool, *argv[1:]])
        if code == 0 and out.strip():
            return pick(out)
    return ""


def _state_value(text: str, key: str) -> str:
    match = re.search(rf"^{key}=([0-9A-Za-z.+_~-]*)$", text, re.MULTILINE)
    return match.group(1) if match else ""


def find_backup(probe: Probe, pinned: Pin) -> tuple[str, bool]:
    """(version of the previous driver kept aside, state file present)."""
    state_file = probe.path("/var/lib/quadsens/driver-state")
    recorded = ""
    present = False
    try:
        recorded = _state_value(state_file.read_text(encoding="utf-8"), "STOCK_VERSION")
        present = True
    except OSError:
        pass
    backup = probe.path("/usr/local/src")
    if recorded and (backup / f"{DKMS_NAME}-{recorded}" / "dkms.conf").is_file():
        return recorded, present
    candidates = []
    try:
        entries = sorted(backup.glob(f"{DKMS_NAME}-*"))
    except OSError:
        entries = []
    for entry in entries:
        version = entry.name[len(DKMS_NAME) + 1:]
        if (version != pinned.version and _VERSION.fullmatch(version)
                and (entry / "dkms.conf").is_file()):
            candidates.append(version)
    return (candidates[0] if len(candidates) == 1 else ""), present


def detect(probe: Probe | None = None, pinned: Pin | None = None) -> SetupState:
    """Look at the system and say what is installed. Changes nothing."""
    probe = probe or Probe()
    pinned = pinned or pin()
    versions: list[tuple[str, bool]] = []
    dkms = probe.which("dkms")
    if dkms is not None:
        _code, out = probe.run([dkms, "status"])
        versions = parse_dkms_status(out, probe.kernel)

    def is_fork(version: str) -> bool:
        return version == pinned.version or "dirscale" in version

    forks = [version for version, _installed in versions if is_fork(version)]
    stocks = [version for version, _installed in versions if not is_fork(version)]
    fork_version = pinned.version if pinned.version in forks else (forks[0] if forks else "")
    stock_version = stocks[0] if stocks else ""
    package = ""
    if stock_version:
        package = package_owner(probe, probe.path(f"/usr/src/{DKMS_NAME}-{stock_version}"))

    if fork_version == pinned.version and dict(versions).get(pinned.version):
        kind = Kind.FORK
    elif fork_version and not stock_version:
        kind = Kind.FORK_OTHER
    elif stock_version:
        kind = Kind.STOCK_PACKAGED if package else Kind.STOCK
    else:
        kind = Kind.NONE

    module_dir = probe.path(f"/sys/module/{MODULE}")
    loaded = module_dir.is_dir()
    loaded_is_fork = (module_dir / "parameters" / DIRSCALE_PARAM).exists()

    tool = ""
    tool_is_fork = False
    for candidate in ("/usr/bin/yeetmousectl", "/usr/local/bin/yeetmousectl"):
        path = probe.path(candidate)
        if path.is_file():
            tool = candidate
            try:
                tool_is_fork = TOOL_MARKER in path.read_bytes()
            except OSError:
                tool_is_fork = False
            break

    group_exists = in_group = group_active = False
    getent = probe.which("getent")
    if getent is not None:
        code, out = probe.run([getent, "group", GROUP])
        fields = out.strip().split(":")
        if code == 0 and len(fields) >= 4:
            group_exists = True
            in_group = probe.user() in [name for name in fields[3].split(",") if name]
            try:
                group_active = int(fields[2]) in probe.session_groups()
            except ValueError:
                group_active = False
    backup_version, recorded = find_backup(probe, pinned)
    return SetupState(
        kind=kind, versions=tuple(versions), fork_version=fork_version,
        stock_version=stock_version, package=package, loaded=loaded,
        loaded_is_fork=loaded_is_fork, tool=tool, tool_is_fork=tool_is_fork,
        service=probe.path("/usr/lib/systemd/system/yeetmouse.service").is_file(),
        group_exists=group_exists, in_group=in_group, group_active=group_active,
        backup_version=backup_version, recorded=recorded)


# ---- prerequisites -----------------------------------------------------------------

# Package names per family of distributions, for the hints.
PACKAGES: dict[str, dict[str, str]] = {
    "arch": {"dkms": "dkms", "git": "git", "make": "make", "cc": "gcc", "c++": "gcc",
             "bash": "bash", "headers": "linux-headers (Manjaro: linuxXY-headers for "
                                        "your kernel)", "pkexec": "polkit"},
    "debian": {"dkms": "dkms", "git": "git", "make": "make", "cc": "gcc", "c++": "g++",
               "bash": "bash", "headers": "linux-headers-$(uname -r)",
               "pkexec": "pkexec (older releases: policykit-1)"},
    "fedora": {"dkms": "dkms", "git": "git", "make": "make", "cc": "gcc", "c++": "gcc-c++",
               "bash": "bash", "headers": "kernel-devel", "pkexec": "polkit"},
}
INSTALL_COMMANDS = {"arch": "sudo pacman -S", "debian": "sudo apt install",
                    "fedora": "sudo dnf install"}


def distro_family(probe: Probe | None = None) -> str:
    """arch, debian or fedora, "" if /etc/os-release names none of them."""
    probe = probe or Probe()
    try:
        text = probe.path("/etc/os-release").read_text(encoding="utf-8")
    except OSError:
        return ""
    names: list[str] = []
    for key in ("ID", "ID_LIKE"):
        match = re.search(rf'^{key}="?([^"\n]*)"?$', text, re.MULTILINE)
        if match:
            names += match.group(1).lower().split()
    for family, members in (("arch", ("arch", "manjaro", "endeavouros")),
                            ("debian", ("debian", "ubuntu", "linuxmint", "pop")),
                            ("fedora", ("fedora", "rhel", "centos", "nobara"))):
        if any(name in members for name in names):
            return family
    return ""


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str          # what was found, or what is missing
    hint: str = ""       # how to get it on this distribution
    required: bool = True

    def text(self) -> str:
        mark = "✓" if self.ok else ("✕" if self.required else "–")
        text = f"{mark} {self.name}: {self.detail}"
        return f"{text} {self.hint}" if self.hint and not self.ok else text


def _hint(family: str, key: str) -> str:
    if family not in PACKAGES:
        return "Install it with your package manager."
    return f"Install it: {INSTALL_COMMANDS[family]} {PACKAGES[family][key]}"


def prerequisites(probe: Probe | None = None) -> list[Check]:
    """Everything the installer needs, each with a pass/fail text and a hint."""
    probe = probe or Probe()
    family = distro_family(probe)
    checks: list[Check] = []

    def tool(name: str, key: str, title: str, alternatives: Sequence[str] = ()) -> None:
        found = next((path for path in (probe.which(candidate)
                                        for candidate in (name, *alternatives))
                      if path), None)
        checks.append(Check(title, found is not None,
                            found or "not installed.", _hint(family, key)))

    tool("dkms", "dkms", "DKMS")
    tool("git", "git", "git")
    tool("make", "make", "make")
    tool("cc", "cc", "C compiler", ("gcc",))
    tool("c++", "c++", "C++ compiler", ("g++",))
    tool("bash", "bash", "bash (for the fork's Makefile)")
    headers = probe.path(f"/lib/modules/{probe.kernel}/build")
    checks.append(Check(
        "Kernel headers", headers.is_dir(),
        f"found for {probe.kernel}." if headers.is_dir()
        else f"missing for the running kernel {probe.kernel} (/lib/modules/"
             f"{probe.kernel}/build).", _hint(family, "headers")))
    tool("pkexec", "pkexec", "pkexec (polkit)")
    systemd = probe.path("/run/systemd/system").is_dir()
    checks.append(Check(
        "systemd", systemd,
        "running." if systemd else "not running: nothing will apply the saved "
                                   "configuration at boot.", required=False))
    return checks


def ready(checks: Sequence[Check]) -> bool:
    return all(check.ok for check in checks if check.required)


# ---- what will happen ----------------------------------------------------------------


def changes(action: str, state: SetupState, pinned: Pin) -> list[str]:
    """Plain-language list of what the action changes on this system."""
    if action == INSTALL:
        lines = [
            f"Downloads the source of the fork from {pinned.url} at commit "
            f"{pinned.commit[:12]} into a private temporary directory and checks that it "
            "is exactly that commit.",
            "Builds the kernel module as root through DKMS and installs it as "
            f"{DKMS_NAME}/{pinned.version}; DKMS rebuilds it for new kernels.",
        ]
        if state.kind is Kind.STOCK:
            lines.append(
                f"Removes the unmodified driver {state.stock_version} from DKMS and moves "
                "its source from /usr/src to /usr/local/src, so that Revert can put it back.")
        lines += [
            "Installs the fork's yeetmousectl to /usr/bin, the service yeetmouse.service "
            "and, if there is none, a default /etc/yeetmouse.conf. An existing "
            "configuration is kept.",
            f"Creates the group {GROUP} if needed and adds you to it.",
            "Reloads the module and restarts the service. Mouse acceleration is off for a "
            "moment while that happens.",
            "Records what it did in /var/lib/quadsens/driver-state.",
        ]
        return lines
    lines = [f"Removes the fork {pinned.version} from DKMS and deletes its source from "
             "/usr/src."]
    if state.backup_version:
        lines += [
            f"Moves the previous driver {state.backup_version} back from /usr/local/src to "
            "/usr/src and installs it through DKMS.",
            "Reloads the module and restarts the service. Mouse acceleration is off for a "
            "moment while that happens.",
            "Leaves the fork's yeetmousectl in place; it works with the unmodified driver.",
        ]
    else:
        lines += [
            "No previous driver is kept, so afterwards no yeetmouse module is installed "
            "and mouse acceleration is off.",
            "Removes the tool and the service if the install by QuadSens added them.",
        ]
    lines.append(f"Keeps /etc/yeetmouse.conf and the group {GROUP}.")
    return lines


# ---- starting the script ---------------------------------------------------------------


@dataclass(frozen=True)
class Launch:
    argv: list[str]
    stdin: str      # text to feed to the process, "" if the script is run from a file
    how: str        # one sentence for the user


def installed_script_ok(text: str, path: Path = INSTALLED_SCRIPT) -> bool:
    """Whether a system-installed copy may be run: root-owned and identical to ours."""
    if not is_trusted_tool(path, (str(path.parent),)):
        return False
    try:
        return path.read_text(encoding="utf-8") == text
    except OSError:
        return False


def launch(action: str, dry_run: bool = False, text: str | None = None,
           pkexec: str = "pkexec", use_installed: bool = False) -> Launch:
    """The command that carries out ``action``.

    Normally the script text goes to the shell's standard input, so there is no
    file that could be exchanged between showing and running it. The dry run
    needs no root rights and is started without pkexec.
    """
    if action not in (INSTALL, REVERT):
        raise SetupError(f"unknown action {action!r}")
    text = script_text() if text is None else text
    if dry_run:
        return Launch([SHELL, "-s", "--", "--dry-run", action], text,
                      "Dry run without root rights: only looks and prints.")
    found = shutil.which(pkexec, path=SAFE_PATH)
    if found is None:
        raise SetupError("pkexec is not installed")
    if use_installed:
        if not installed_script_ok(text):
            raise SetupError(f"{INSTALLED_SCRIPT} is missing, not root-owned or differs "
                             "from the script shown here")
        return Launch([found, "--disable-internal-agent", str(INSTALLED_SCRIPT), action],
                      "", f"Runs the installed copy {INSTALLED_SCRIPT} through pkexec.")
    return Launch([found, "--disable-internal-agent", SHELL, "-s", "--", action], text,
                  "Runs through pkexec; the text above is fed to /bin/sh on its standard "
                  "input, so exactly what is shown is what runs.")
