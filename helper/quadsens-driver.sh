#!/bin/sh
# quadsens-driver.sh - install or revert the yeetmouse fork with directional scaling.
#
#   quadsens-driver.sh [--dry-run] install
#   quadsens-driver.sh [--dry-run] revert
#
# QuadSens feeds this text to "pkexec /bin/sh -s -- install" (or revert), so it
# runs as root. It downloads the source of the fork at ONE pinned commit, checks
# that it got exactly that commit, builds the kernel module through DKMS and
# installs the fork's command line tool, service and group the way the fork's
# own Makefile does. "revert" puts the previous driver back.
#
# --dry-run only looks at the system and prints what would be done. It needs no
# root rights and changes nothing.
#
# Everything is inside main() and main is called on the last line with its
# standard input closed. A shell that reads a script from standard input reads
# it piece by piece; this way the whole text is read before anything runs and
# no command can swallow part of it.
#
# SPDX-License-Identifier: GPL-3.0-or-later

set -eu

FORK_URL="https://github.com/freasy/YeetMouse.git"
FORK_COMMIT="2a3e21956ef5a2ddfc4523a7ad4ef8117f6f4913"
FORK_VERSION="0.9.5+dirscale1"
DKMS_NAME="yeetmouse-driver"
MODULE="yeetmouse"
GROUP="yeetmouse"
SERVICE="yeetmouse.service"

main() {
    # Space, tab, newline: the default, whatever the caller had.
    IFS="$(printf ' \t\nx')"
    IFS="${IFS%x}"
    umask 022

    # ---- test hook ---------------------------------------------------------
    # The tests run this script without root against a fake directory tree and
    # stand-in tools. That is refused for root: pkexec runs as uid 0 with a
    # scrubbed environment, so a real run can never take this branch.
    ROOT=""
    TESTING=0
    if [ -n "${QUADSENS_TEST_ROOT:-}" ]; then
        if [ "$(id -u)" -eq 0 ]; then
            echo "error: QUADSENS_TEST_ROOT is not allowed as root." >&2
            exit 2
        fi
        case "$QUADSENS_TEST_ROOT" in
            /*) ;;
            *) echo "error: QUADSENS_TEST_ROOT must be an absolute path." >&2; exit 2 ;;
        esac
        ROOT="$QUADSENS_TEST_ROOT"
        TESTING=1
        # Stand-in tools live in one directory; only they are used for the
        # tools below, so a test can never reach the real dkms or the network.
        STUBS="${QUADSENS_TEST_PATH:-/nonexistent}"
        PATH="$STUBS:/usr/bin:/bin"
    else
        STUBS=""
        PATH="/usr/sbin:/usr/bin:/sbin:/bin"
    fi
    export PATH

    # ---- arguments -----------------------------------------------------------
    DRY=0
    ACTION=""
    for arg in "$@"; do
        case "$arg" in
            --dry-run) DRY=1 ;;
            install|revert)
                [ -z "$ACTION" ] || usage
                ACTION="$arg" ;;
            *) usage ;;
        esac
    done
    [ -n "$ACTION" ] || usage

    if [ "$DRY" -eq 0 ] && [ "$TESTING" -eq 0 ] && [ "$(id -u)" -ne 0 ]; then
        die "this has to run as root (QuadSens starts it through pkexec)."
    fi

    # ---- fixed locations -------------------------------------------------------
    SRC_DIR="$ROOT/usr/src"
    BACKUP_DIR="$ROOT/usr/local/src"
    STATE_DIR="$ROOT/var/lib/quadsens"
    STATE_FILE="$STATE_DIR/driver-state"
    CTL="$ROOT/usr/bin/yeetmousectl"
    UNIT="$ROOT/usr/lib/systemd/system/$SERVICE"
    SYSUSERS="$ROOT/usr/lib/sysusers.d/yeetmouse.conf"
    CONFIG="$ROOT/etc/yeetmouse.conf"
    PARAMS="$ROOT/sys/module/$MODULE/parameters"
    FORK_SRC="$SRC_DIR/$DKMS_NAME-$FORK_VERSION"
    KERNEL="$(uname -r)"
    HEADERS="$ROOT/lib/modules/$KERNEL/build"

    # ---- tools, resolved once ----------------------------------------------------
    T_DKMS="$(tool dkms)"
    T_GIT="$(tool git)"
    T_MAKE="$(tool make)"
    T_MODPROBE="$(tool modprobe)"
    T_RMMOD="$(tool rmmod)"
    T_GETENT="$(tool getent)"
    T_SYSTEMCTL="$(tool systemctl)"
    T_GROUPADD="$(tool groupadd)"
    T_USERMOD="$(tool usermod)"
    T_PACMAN="$(tool pacman)"
    T_DPKG="$(tool dpkg)"
    T_RPM="$(tool rpm)"

    WORK=""
    STOCK_MOVED=""        # version of a previous driver that this run moved aside
    FORK_REGISTERED=0     # this run registered the fork with DKMS
    FAILED_STATE=""
    trap 'on_exit' EXIT
    trap 'exit 130' INT TERM HUP

    case "$ACTION" in
        install) do_install ;;
        revert) do_revert ;;
    esac
}

# ---- helpers -------------------------------------------------------------------

usage() {
    echo "usage: quadsens-driver.sh [--dry-run] install|revert" >&2
    exit 2
}

die() {
    echo "error: $*" >&2
    exit 1
}

# Absolute path of a tool, or nothing if it is not installed.
tool() {
    if [ "$TESTING" -eq 1 ]; then
        if [ -x "$STUBS/$1" ]; then
            echo "$STUBS/$1"
        fi
        return 0
    fi
    command -v "$1" 2>/dev/null || true
}

need() {
    # need NAME PATH: stop if a required tool is missing.
    [ -n "$2" ] || die "$1 is not installed."
}

STEP=0
step() {
    STEP=$((STEP + 1))
    echo "[$STEP/$STEPS] $*"
}

note() {
    echo "      $*"
}

# Run a command that changes the system; with --dry-run only print it.
act() {
    if [ "$DRY" -eq 1 ]; then
        echo "      would run: $*"
        return 0
    fi
    echo "      + $*"
    clean "$@"
}

# Run a command with a minimal environment, whatever the caller had set.
clean() {
    if [ "$TESTING" -eq 1 ]; then
        env -i PATH="$PATH" LC_ALL=C HOME="${WORK:-/nonexistent}" \
            GIT_TERMINAL_PROMPT=0 GIT_CONFIG_NOSYSTEM=1 \
            QUADSENS_TEST_ROOT="$ROOT" QUADSENS_STUB_LOG="${QUADSENS_STUB_LOG:-}" \
            QUADSENS_STUB_FAIL="${QUADSENS_STUB_FAIL:-}" \
            QUADSENS_STUB_HEAD="${QUADSENS_STUB_HEAD:-}" \
            QUADSENS_STUB_VERSION="${QUADSENS_STUB_VERSION:-}" "$@"
    else
        env -i PATH="$PATH" LC_ALL=C HOME="${WORK:-/nonexistent}" \
            GIT_TERMINAL_PROMPT=0 GIT_CONFIG_NOSYSTEM=1 "$@"
    fi
}

# A DKMS version string as it may appear in a path: no slash, no dots only.
valid_version() {
    case "$1" in
        ""|.|..|*/*|*[!0-9A-Za-z.+_~-]*) return 1 ;;
        *) return 0 ;;
    esac
}

# Versions of the module that DKMS knows about, one per line.
dkms_versions() {
    [ -n "$T_DKMS" ] || return 0
    clean "$T_DKMS" status 2>/dev/null \
        | sed -n "s|^${DKMS_NAME}[/, ]*\\([^,: ]*\\).*|\\1|p" | sort -u
}

# True if DKMS has the given version built and installed for the running kernel.
dkms_installed() {
    [ -n "$T_DKMS" ] || return 1
    clean "$T_DKMS" status 2>/dev/null \
        | grep -F "$DKMS_NAME" | grep -F "$1" | grep -F "$KERNEL" | grep -q "installed"
}

# Name of the distribution package that owns a path, or nothing.
package_owner() {
    if [ -n "$T_PACMAN" ]; then
        clean "$T_PACMAN" -Qqo -- "$1" 2>/dev/null && return 0
    fi
    if [ -n "$T_DPKG" ]; then
        clean "$T_DPKG" -S -- "$1" 2>/dev/null | sed -n '1s/:.*//p' && return 0
    fi
    if [ -n "$T_RPM" ]; then
        clean "$T_RPM" -qf -- "$1" 2>/dev/null && return 0
    fi
    return 0
}

# Read one value from the state file. Only plain KEY=VALUE lines are accepted;
# the file is never executed.
state_get() {
    [ -f "$STATE_FILE" ] || return 0
    sed -n "s/^$1=\\([0-9A-Za-z.+_~-]*\\)\$/\\1/p" "$STATE_FILE" | head -n 1
}

# The previous driver that was moved aside: from the state file, or a single
# matching directory in the backup location (a setup done by hand).
find_stock_backup() {
    recorded="$(state_get STOCK_VERSION)"
    if [ -n "$recorded" ] && valid_version "$recorded" \
        && [ -f "$BACKUP_DIR/$DKMS_NAME-$recorded/dkms.conf" ]; then
        echo "$recorded"
        return 0
    fi
    found=""
    count=0
    for dir in "$BACKUP_DIR/$DKMS_NAME"-*; do
        [ -f "$dir/dkms.conf" ] || continue
        version="${dir##*/"$DKMS_NAME"-}"
        valid_version "$version" || continue
        [ "$version" != "$FORK_VERSION" ] || continue
        found="$version"
        count=$((count + 1))
    done
    if [ "$count" -eq 1 ]; then
        echo "$found"
    fi
    return 0
}

# Name of the user who started pkexec, from the user database; never from an argument.
invoking_user() {
    uid="${PKEXEC_UID:-}"
    case "$uid" in
        ""|*[!0-9]*) return 0 ;;
    esac
    [ "$uid" -ne 0 ] || return 0
    clean "$T_GETENT" passwd "$uid" 2>/dev/null | cut -d: -f1 | head -n 1
}

has_systemd() {
    [ -n "$T_SYSTEMCTL" ] && [ -d "$ROOT/run/systemd/system" ]
}

reload_module() {
    act "$T_MODPROBE" -r "$MODULE" || act "$T_RMMOD" "$MODULE" || true
    act "$T_MODPROBE" "$MODULE"
}

unload_module() {
    act "$T_MODPROBE" -r "$MODULE" || act "$T_RMMOD" "$MODULE" || true
}

restart_service() {
    if has_systemd; then
        act "$T_SYSTEMCTL" daemon-reload
        if [ -f "$UNIT" ] || [ "$DRY" -eq 1 ]; then
            act "$T_SYSTEMCTL" enable "$SERVICE"
            act "$T_SYSTEMCTL" restart "$SERVICE" || note "the service did not start; see: systemctl status $SERVICE"
        fi
    else
        note "systemd is not running here: nothing applies $CONFIG at boot."
    fi
}

write_state() {
    # write_state KEY VALUE: replace one line of the state file.
    if [ "$DRY" -eq 1 ]; then
        echo "      would record: $1=$2"
        return 0
    fi
    mkdir -p "$STATE_DIR"
    chmod 755 "$STATE_DIR"
    tmp="$STATE_FILE.new"
    { [ -f "$STATE_FILE" ] && grep -v "^$1=" "$STATE_FILE" || true; echo "$1=$2"; } > "$tmp"
    chmod 644 "$tmp"
    mv -f "$tmp" "$STATE_FILE"
    echo "      recorded: $1=$2"
}

# Runs on every exit: clean up, and after a failed install put things back.
on_exit() {
    status=$?
    trap - EXIT
    if [ "$status" -ne 0 ] && [ "$ACTION" = "install" ] && [ "$DRY" -eq 0 ]; then
        rollback_install || true
    fi
    if [ -n "$WORK" ] && [ -d "$WORK" ]; then
        rm -rf "$WORK"
    fi
    if [ "$status" -ne 0 ]; then
        echo "FAILED (exit status $status)."
        [ -z "$FAILED_STATE" ] || echo "State: $FAILED_STATE"
    fi
    exit "$status"
}

rollback_install() {
    if [ "$FORK_REGISTERED" -eq 0 ] && [ -z "$STOCK_MOVED" ]; then
        FAILED_STATE="nothing was changed; the driver is as it was before."
        return 0
    fi
    echo "Install failed. Putting the previous state back ..."
    if [ "$FORK_REGISTERED" -eq 1 ]; then
        act "$T_DKMS" remove "$DKMS_NAME/$FORK_VERSION" --all || true
        act rm -rf "$FORK_SRC" || true
    fi
    if [ -n "$STOCK_MOVED" ]; then
        if act mv "$BACKUP_DIR/$DKMS_NAME-$STOCK_MOVED" "$SRC_DIR/" \
            && act "$T_DKMS" install "$DKMS_NAME/$STOCK_MOVED"; then
            act rm -f "$STATE_FILE" || true
            act "$T_MODPROBE" "$MODULE" || true
            FAILED_STATE="the previous driver $STOCK_MOVED is installed again."
        else
            FAILED_STATE="NO yeetmouse module is installed. The previous driver $STOCK_MOVED could not be put back automatically; its source is in $BACKUP_DIR or $SRC_DIR. Run the revert, or: dkms install $DKMS_NAME/$STOCK_MOVED"
        fi
    else
        FAILED_STATE="the fork was removed again; there was no previous driver."
    fi
}

# ---- install ---------------------------------------------------------------------

do_install() {
    STEPS=9
    echo "Install the yeetmouse fork $FORK_VERSION"
    echo "Source: $FORK_URL at commit $FORK_COMMIT"
    [ "$DRY" -eq 0 ] || echo "Dry run: nothing is changed."

    step "Checking what is needed"
    need dkms "$T_DKMS"
    need git "$T_GIT"
    need make "$T_MAKE"
    need modprobe "$T_MODPROBE"
    need getent "$T_GETENT"
    need groupadd "$T_GROUPADD"
    need usermod "$T_USERMOD"
    [ -n "$(tool bash)" ] || die "bash is not installed (the fork's Makefile needs it)."
    [ -n "$(tool cc)" ] || [ -n "$(tool gcc)" ] || die "no C compiler (cc or gcc) is installed."
    [ -n "$(tool c++)" ] || [ -n "$(tool g++)" ] || die "no C++ compiler (c++ or g++) is installed."
    [ -d "$HEADERS" ] || die "kernel headers for $KERNEL are missing ($HEADERS)."
    note "tools and kernel headers for $KERNEL are present."

    step "Looking at the installed driver"
    others=""
    fork_known=0
    for version in $(dkms_versions); do
        valid_version "$version" || die "DKMS reports a version that cannot be handled: $version"
        if [ "$version" = "$FORK_VERSION" ]; then
            fork_known=1
        else
            others="$others $version"
        fi
    done
    # shellcheck disable=SC2086  # a list of validated version strings
    set -- $others
    [ "$#" -le 1 ] || die "more than one other version is installed ($*). Remove all but one with: dkms remove $DKMS_NAME/<version> --all"
    for version in $others; do
        owner="$(package_owner "$SRC_DIR/$DKMS_NAME-$version")"
        [ -z "$owner" ] || die "the installed driver $version belongs to the package \"$owner\". Remove that package with your package manager first, then run this again."
    done
    for path in "$CTL" "$UNIT"; do
        if [ -e "$path" ]; then
            owner="$(package_owner "$path")"
            [ -z "$owner" ] || die "$path belongs to the package \"$owner\". Remove that package with your package manager first, then run this again."
        fi
    done
    for version in $others; do
        [ ! -e "$BACKUP_DIR/$DKMS_NAME-$version" ] \
            || die "$BACKUP_DIR/$DKMS_NAME-$version exists already, so the installed driver $version cannot be moved there. Move or remove that directory first."
    done
    fork_ok=0
    if [ "$fork_known" -eq 1 ] && dkms_installed "$FORK_VERSION"; then
        fork_ok=1
    fi
    user="$(invoking_user)"
    user_ok=1
    if [ -n "$user" ] && ! clean "$T_GETENT" group "$GROUP" 2>/dev/null \
        | cut -d: -f4 | tr ',' '\n' | grep -qxF "$user"; then
        user_ok=0
    fi
    if [ "$fork_ok" -eq 1 ]; then
        note "the fork $FORK_VERSION is installed for kernel $KERNEL."
    elif [ -n "$others" ]; then
        note "installed now:$others (will be moved aside and can be restored with revert)."
    else
        note "no yeetmouse driver is installed through DKMS."
    fi

    if [ "$fork_ok" -eq 1 ] && [ -z "$others" ] && [ -x "$CTL" ] \
        && grep -q dirScaleXPos "$CTL" 2>/dev/null \
        && { [ -f "$UNIT" ] || ! has_systemd; } \
        && clean "$T_GETENT" group "$GROUP" >/dev/null 2>&1 && [ "$user_ok" -eq 1 ]; then
        echo "Nothing to do: the fork, its tool, the service and the group are in place."
        echo "DONE."
        return 0
    fi

    step "Downloading the pinned source"
    if [ "$DRY" -eq 1 ]; then
        echo "      would create a private work directory (mktemp -d, mode 0700)"
        echo "      would run: git fetch --depth 1 $FORK_URL $FORK_COMMIT"
        echo "      would check that the checked-out commit is $FORK_COMMIT"
        SOURCE="<work directory>/src"
    else
        WORK="$(mktemp -d "$ROOT/tmp/quadsens-driver.XXXXXXXX")"
        chmod 700 "$WORK"
        SOURCE="$WORK/src"
        act "$T_GIT" init -q "$SOURCE"
        act "$T_GIT" -C "$SOURCE" fetch -q --depth 1 "$FORK_URL" "$FORK_COMMIT"
        act "$T_GIT" -C "$SOURCE" checkout -q --detach FETCH_HEAD
        head="$(clean "$T_GIT" -C "$SOURCE" rev-parse HEAD)"
        [ "$head" = "$FORK_COMMIT" ] \
            || die "the downloaded source is commit \"$head\", not the pinned $FORK_COMMIT. Nothing was installed."
        note "verified: commit $head"
        got="$(sed -n 's/^YEETMOUSE_VERSION[[:space:]]*:=[[:space:]]*//p' "$SOURCE/version.mk" | head -n 1)"
        [ "$got" = "$FORK_VERSION" ] \
            || die "the source says version \"$got\", expected $FORK_VERSION. Nothing was installed."
    fi

    step "Building the command line tool"
    # Built before anything on the system changes: a compiler problem stops here.
    if [ "$DRY" -eq 1 ]; then
        echo "      would run: make -C $SOURCE yeetmousectl"
    else
        act "$T_MAKE" -C "$SOURCE" yeetmousectl
    fi

    step "Group $GROUP"
    if clean "$T_GETENT" group "$GROUP" >/dev/null 2>&1; then
        note "the group exists."
    else
        act "$T_GROUPADD" -r "$GROUP"
    fi
    if [ -z "$user" ]; then
        note "could not tell who started this; add yourself with: usermod -aG $GROUP <user>"
    elif [ "$user_ok" -eq 1 ]; then
        note "$user is a member."
    else
        act "$T_USERMOD" -aG "$GROUP" "$user"
    fi

    step "Building the kernel module"
    if [ "$fork_ok" -eq 1 ]; then
        note "already built and installed for $KERNEL."
    else
        FORK_REGISTERED=1
        act "$T_MAKE" -C "$SOURCE" setup_dkms DESTDIR="$ROOT/"
        [ "$fork_known" -eq 1 ] || act "$T_DKMS" add "$DKMS_NAME/$FORK_VERSION"
        # Built while the previous driver is still in place: a build problem
        # stops here and leaves that driver untouched.
        act "$T_DKMS" build "$DKMS_NAME/$FORK_VERSION"
    fi

    step "Replacing the previous driver"
    if [ -z "$others" ]; then
        note "there is none."
    fi
    for version in $others; do
        act "$T_DKMS" remove "$DKMS_NAME/$version" --all
        act mkdir -p "$BACKUP_DIR"
        act mv "$SRC_DIR/$DKMS_NAME-$version" "$BACKUP_DIR/"
        STOCK_MOVED="$version"
        write_state STOCK_VERSION "$version"
    done
    if [ "$fork_ok" -eq 0 ]; then
        act "$T_DKMS" install "$DKMS_NAME/$FORK_VERSION"
    fi

    step "Installing tool, configuration and service"
    [ -e "$CTL" ] || write_state CREATED_CTL 1
    [ -e "$UNIT" ] || write_state CREATED_SERVICE 1
    [ -e "$SYSUSERS" ] || write_state CREATED_SYSUSERS 1
    if [ -f "$CONFIG" ]; then
        note "keeping the existing $CONFIG."
    else
        write_state CREATED_CONFIG 1
    fi
    act "$T_MAKE" -C "$SOURCE" install_userspace install_config install_service DESTDIR="$ROOT/"
    write_state FORK_COMMIT "$FORK_COMMIT"

    step "Loading the new module"
    note "mouse acceleration is off for a moment while the module is reloaded."
    reload_module
    if [ -d "$PARAMS" ] && [ "$DRY" -eq 0 ]; then
        chgrp "$GROUP" "$PARAMS"/* 2>/dev/null || true
        chmod 0660 "$PARAMS"/* 2>/dev/null || true
    fi
    restart_service

    # From here on a failure must not undo a working install.
    FORK_REGISTERED=0
    STOCK_MOVED=""
    echo "DONE. The yeetmouse fork $FORK_VERSION is installed."
    [ -z "$others" ] || echo "The previous driver is kept in $BACKUP_DIR; \"revert\" puts it back."
    echo "Log out and in again, so that your session is in group $GROUP."
}

# ---- revert ----------------------------------------------------------------------

do_revert() {
    STEPS=5
    echo "Revert the yeetmouse fork $FORK_VERSION"
    [ "$DRY" -eq 0 ] || echo "Dry run: nothing is changed."

    step "Checking what is needed"
    need dkms "$T_DKMS"
    need modprobe "$T_MODPROBE"
    note "tools are present."

    step "Looking at the installed driver"
    fork_known=0
    for version in $(dkms_versions); do
        [ "$version" != "$FORK_VERSION" ] || fork_known=1
    done
    stock="$(find_stock_backup)"
    if [ "$fork_known" -eq 0 ] && [ ! -d "$FORK_SRC" ] && [ -z "$stock" ]; then
        echo "Nothing to do: the fork is not installed and no previous driver is kept."
        echo "DONE."
        return 0
    fi
    [ "$fork_known" -eq 0 ] || note "the fork $FORK_VERSION is registered with DKMS."
    if [ -n "$stock" ]; then
        note "previous driver kept in $BACKUP_DIR: $stock"
        [ ! -e "$SRC_DIR/$DKMS_NAME-$stock" ] \
            || die "$SRC_DIR/$DKMS_NAME-$stock exists already, so the kept driver cannot be moved back. Remove one of the two first."
    else
        note "no previous driver is kept: after this no yeetmouse module is installed."
    fi

    step "Removing the fork's kernel module"
    if [ "$fork_known" -eq 1 ]; then
        act "$T_DKMS" remove "$DKMS_NAME/$FORK_VERSION" --all
    else
        note "DKMS does not know it."
    fi
    if [ -d "$FORK_SRC" ] || [ "$DRY" -eq 1 ]; then
        act rm -rf "$FORK_SRC"
    fi

    step "Previous driver"
    if [ -n "$stock" ]; then
        FAILED_STATE="NO yeetmouse module is installed. The previous driver $stock is in $BACKUP_DIR or $SRC_DIR; put it back with: dkms install $DKMS_NAME/$stock"
        act mv "$BACKUP_DIR/$DKMS_NAME-$stock" "$SRC_DIR/"
        act "$T_DKMS" install "$DKMS_NAME/$stock"
        FAILED_STATE=""
        note "the fork's yeetmousectl stays; it works with the unmodified driver too."
    else
        # Take away what the install added, as far as it was recorded.
        unload_module
        if [ "$(state_get CREATED_SERVICE)" = "1" ]; then
            if has_systemd; then
                act "$T_SYSTEMCTL" disable --now "$SERVICE" || true
            fi
            act rm -f "$UNIT"
        fi
        [ "$(state_get CREATED_SYSUSERS)" != "1" ] || act rm -f "$SYSUSERS"
        [ "$(state_get CREATED_CTL)" != "1" ] || act rm -f "$CTL"
        note "$CONFIG and the group $GROUP are left in place."
    fi

    step "Loading the module"
    if [ -n "$stock" ]; then
        note "mouse acceleration is off for a moment while the module is reloaded."
        reload_module
        restart_service
    else
        if has_systemd; then
            act "$T_SYSTEMCTL" daemon-reload || true
        fi
        note "there is no yeetmouse module to load."
    fi
    act rm -f "$STATE_FILE"

    if [ -n "$stock" ]; then
        echo "DONE. The previous driver $stock is installed again."
    else
        echo "DONE. The fork is removed. No yeetmouse module is installed now."
    fi
}

main "$@" </dev/null; exit $?
