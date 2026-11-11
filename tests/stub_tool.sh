#!/bin/sh
# Stand-in for the system tools the driver script calls (dkms, git, make, ...).
# Copied under each tool's name by tests/test_driver_setup.py. Every call is
# appended to $QUADSENS_STUB_LOG as "name arguments"; nothing real is touched.
#
#   QUADSENS_TEST_ROOT   the fake directory tree
#   QUADSENS_STUB_FAIL   a call that starts with this text fails with status 9
#   QUADSENS_STUB_HEAD   what "git rev-parse HEAD" answers (default: the pin)
set -eu
name="${0##*/}"
root="${QUADSENS_TEST_ROOT:?}"
call="$name $*"
case "$name $*" in
    "dkms status"|"getent "*|"pacman "*|"git "*" rev-parse HEAD") ;;   # queries: not logged
    *) echo "$call" >> "${QUADSENS_STUB_LOG:?}" ;;
esac
if [ -n "${QUADSENS_STUB_FAIL:-}" ]; then
    case "$call" in
        "$QUADSENS_STUB_FAIL"*) echo "$name: simulated failure" >&2; exit 9 ;;
    esac
fi
fork="yeetmouse-driver-0.9.5+dirscale1"
case "$name" in
    dkms)
        case "${1:-}" in
            status) cat "$root/dkms-status" 2>/dev/null || true ;;
        esac ;;
    git)
        case "$*" in
            "init -q "*) mkdir -p "$3" ;;
            *" checkout "*)
                version="${QUADSENS_STUB_VERSION:-}"
                echo "YEETMOUSE_VERSION := ${version:-0.9.5+dirscale1}" > "$2/version.mk" ;;
            *" rev-parse HEAD") echo "${QUADSENS_STUB_HEAD:-2a3e21956ef5a2ddfc4523a7ad4ef8117f6f4913}" ;;
        esac ;;
    make)
        case "$*" in
            *setup_dkms*) mkdir -p "$root/usr/src/$fork"; echo stub > "$root/usr/src/$fork/dkms.conf" ;;
            *install_userspace*)
                mkdir -p "$root/usr/bin" "$root/usr/lib/systemd/system" "$root/usr/lib/sysusers.d" "$root/etc"
                echo "stub dirScaleXPos" > "$root/usr/bin/yeetmousectl"; chmod 755 "$root/usr/bin/yeetmousectl"
                echo unit > "$root/usr/lib/systemd/system/yeetmouse.service"
                echo "g yeetmouse - -" > "$root/usr/lib/sysusers.d/yeetmouse.conf"
                [ -f "$root/etc/yeetmouse.conf" ] || echo "sens=1" > "$root/etc/yeetmouse.conf" ;;
        esac ;;
    getent)
        case "$1" in
            group) [ -f "$root/group" ] && cat "$root/group" || exit 2 ;;
            passwd) echo "tester:x:$2:$2::/home/tester:/bin/sh" ;;
        esac ;;
    pacman)
        # pacman -Qqo -- PATH: owned if the path is listed in $root/packaged
        if [ -f "$root/packaged" ] && grep -qxF "$3" "$root/packaged"; then
            echo "yeetmouse-driver-dkms"
        else
            exit 1
        fi ;;
esac
exit 0
