"""The two Windows tools QuadSens can fetch: what exactly, from where (no Qt).

QuadSens installs only the releases named here: the exact file, checked by
size and SHA-256. A newer release of a tool is shown as a notice with a link
until a QuadSens update names it here. Nothing in this module touches the
network or the disk.

How the pins were made (2026-11-11): name, size and digest were read from
the GitHub releases API, each file was downloaded once and hashed, and both
agreed.

usbip-win2 0.9.8.1 (https://github.com/vadimgrn/usbip-win2), read in its
README and userspace/innosetup/setup.iss:
* An Inno Setup installer; needs administrator rights; installs into
  Program Files\\USBip; restarts all USB 3.0 hubs while it installs, so USB
  devices drop out for a moment; ends by asking to restart the computer
  (AlwaysRestart=yes). Its README advises creating a restore point first.
* Installing over an older version works: the installer removes the
  previous version itself before it installs.
* The installer file carries an Authenticode signature. Its signer, read from
  the downloaded file, is "Cloudyne Systems (Scheibling Consulting AB)"
  (issued by GlobalSign; the project signs through the Open Source Codesigning
  Initiative).
* Removed through Windows Settings > Apps > Installed apps.
* License: BSD-2-Clause.

RawAccel 1.7.1 (https://github.com/RawAccelOfficial/rawaccel), read in
doc/Guide.md of the release and installer/installer.cpp at the tag:
* A zip with one top folder "RawAccel". The folder is kept wherever the user
  likes; settings.json is written into it by rawaccel.exe on its first start.
* installer.exe must be run once as administrator, from that folder: it
  copies driver\\rawaccel.sys into the system's driver folder, registers the
  driver and adds it as a filter of the mouse class. It takes effect after a
  restart of the computer. It always ends with status 0, also after an error.
* Prerequisites named by the project: Windows 10 or 11, the Visual C++ 2019
  runtime and the .NET Framework 4.7.2 or newer.
* Only driver\\rawaccel.sys is signed (by Microsoft's hardware compatibility
  publisher). installer.exe and the other programs are not signed, so the
  checksum of the zip and of each file is the only check there is.
* Removed with uninstaller.exe from the same folder, then a restart; the
  folder is deleted by hand.
* License: MIT.

QuadSens downloads these tools from their projects. It does not contain or
redistribute them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

USBIP, RAWACCEL = "usbip-win2", "rawaccel"
# The download starts at github.com and is redirected once, to the host that
# serves release files (seen in our own downloads of both pins). No other host
# is followed.
DOWNLOAD_HOSTS = ("github.com", "release-assets.githubusercontent.com")
API_HOST = "api.github.com"


@dataclass(frozen=True)
class Pin:
    key: str
    title: str                  # as shown to the user
    repo: str                   # owner/name on GitHub
    tag: str                    # the release tag
    version: str                # the same as numbers, for comparing
    asset: str                  # exact file name of the release asset
    size: int                   # bytes
    sha256: str                 # of the asset, lower-case hex
    license: str
    tested: str                 # what QuadSens was tested with, one sentence
    signer: str = ""            # expected signer of an installer, "" if it is not signed
    # For an archive: relative path -> (size, sha256) of the files that are
    # run or loaded. They are checked again right before the installer starts.
    files: dict[str, tuple[int, str]] = field(default_factory=dict)

    @property
    def download_url(self) -> str:
        return "https://github.com/%s/releases/download/%s/%s" % (
            self.repo, self.tag, self.asset)

    @property
    def release_url(self) -> str:
        return "https://github.com/%s/releases/tag/%s" % (self.repo, self.tag)

    @property
    def releases_url(self) -> str:
        return "https://github.com/%s/releases" % self.repo

    @property
    def latest_api_url(self) -> str:
        return "https://%s/repos/%s/releases/latest" % (API_HOST, self.repo)


PINS: dict[str, Pin] = {
    USBIP: Pin(
        key=USBIP,
        title="usbip-win2",
        repo="vadimgrn/usbip-win2",
        tag="v.0.9.8.1",
        version="0.9.8.1",
        asset="USBip-0.9.8.1-x64.exe",
        size=26083632,
        sha256="38cad6d4432b52d5bb9409d9ad03b72fdffc4ada4cd3a48fbeca1a2752a8518a",
        license="BSD-2-Clause",
        tested="The command line of usbip.exe was read in the sources of this project. "
               "Attaching through its app was tried on one Windows 11 PC; with which "
               "version is not recorded.",
        signer="Cloudyne Systems (Scheibling Consulting AB)",
    ),
    RAWACCEL: Pin(
        key=RAWACCEL,
        title="RawAccel",
        repo="RawAccelOfficial/rawaccel",
        tag="v1.7.1",
        version="1.7.1",
        asset="RawAccel_v1.7.1.zip",
        size=1542808,
        sha256="770fe3ae0919ca3c4d412f58c985eb27f5434decad809f7e8206de4e8852eec4",
        license="MIT",
        tested="QuadSens writes the setting names of this release.",
        files={
            "installer.exe":
                (39424, "b3b4948de9470a5727390ed016507b59d2c628463e42aaf1b75e906ee04f58a9"),
            "driver/rawaccel.sys":
                (57032, "8a62c4deef2774b43a7363b352eda79897533a1080c9c26ffeff0559e43358d7"),
            "rawaccel.exe":
                (445952, "64cbf09cbb181b81abd3432e9184b896dd227ec258deda547074f5985cfad88d"),
            "writer.exe":
                (5632, "b53e29c7689b9d72838ffbfd1502b8d0407160d3f77b54eb15a7c632f689aa04"),
            "wrapper.dll":
                (486912, "e17d540fb0bb5e47ba7c8e01aff39f1570bfb7ef673a1ddc0bccd2a26f9efb78"),
            "Newtonsoft.Json.dll":
                (701992, "b624949df8b0e3a6153fdfb730a7c6f4990b6592ee0d922e1788433d276610f3"),
            "uninstaller.exe":
                (37376, "62090da1ecd73aa18c2119650a4384d80691fcbabb90716cf5f731ed084a51ca"),
        },
    ),
}
# The archive of RawAccel: everything lies in this one folder, which is left
# out when unpacking, and this is as much as it may hold.
RAWACCEL_TOP_FOLDER = "RawAccel"
RAWACCEL_MAX_FILES = 64
RAWACCEL_MAX_BYTES = 8 * 1024 * 1024
RAWACCEL_INSTALLER = "installer.exe"
RAWACCEL_DRIVER = "driver/rawaccel.sys"

HOW_TO_REMOVE = (
    "usbip-win2: Windows Settings > Apps > Installed apps, find USBip and choose "
    "Uninstall.",
    "RawAccel: run uninstaller.exe in the RawAccel folder (it asks for administrator "
    "rights), restart the computer, then delete the folder.",
    "QuadSens does not remove either of them.",
)


def parse_version(text: str) -> tuple[int, ...] | None:
    """The numbers of a version or release tag ("v.0.9.8.1", "1.7.1"), None if there are none."""
    # Numbers with dots first ("usbip-win2 0.9.8.1" must not give 2); a lone
    # number only when the text holds no other.
    match = re.search(r"\d+(?:\.\d+)+", text or "") \
        or re.fullmatch(r"\D*(\d+)\D*", text or "")
    if not match:
        return None
    if match.re.groups:
        return (int(match.group(1)),)
    return tuple(int(part) for part in match.group(0).split("."))


OLDER, SAME, NEWER, UNKNOWN = "older", "same", "newer", "unknown"


def compare(version: str, pin: Pin) -> str:
    """How an installed or released version relates to the pinned one."""
    have, want = parse_version(version), parse_version(pin.version)
    if have is None or want is None:
        return UNKNOWN
    length = max(len(have), len(want))
    have += (0,) * (length - len(have))
    want += (0,) * (length - len(want))
    return SAME if have == want else OLDER if have < want else NEWER


def update_notice(pin: Pin, latest_tag: str | None) -> str:
    """One quiet sentence when the project has a newer release than the pin, else ""."""
    if not latest_tag or compare(latest_tag, pin) != NEWER:
        return ""
    numbers = parse_version(latest_tag)
    assert numbers is not None
    return "A newer %s %s is available; QuadSens was tested with %s." % (
        pin.title, ".".join(str(n) for n in numbers), pin.version)
