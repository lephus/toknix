#!/bin/sh
# Install toknix, then its Claude Code hooks and rules block (A8).
#
#   curl -fsSL https://raw.githubusercontent.com/lephus/toknix/main/install.sh | sh
#   curl -fsSL https://raw.githubusercontent.com/lephus/toknix/main/install.sh | sh -s -- --dry-run
#
# Options:
#   --dry-run       print what would be done, change nothing
#   --uninstall     remove the hooks, the rules block and toknix itself
#   --from SOURCE   install from SOURCE (a local directory or a pip/uv URL) instead of GitHub
#   --no-hooks      install the toknix command only; skip `toknix install`
# Environment: TOKNIX_REPO (owner/name, default lephus/toknix), TOKNIX_REF (default main).
#
# Uses `uv tool install` when uv is on PATH; otherwise a private venv in ~/.toknix/venv (so PEP 668
# "externally managed" Pythons, e.g. Ubuntu 24.04, are never touched) linked from ~/.local/bin/toknix.
set -eu

REPO="${TOKNIX_REPO:-lephus/toknix}"
REF="${TOKNIX_REF:-main}"
SOURCE="git+https://github.com/${REPO}@${REF}"
DRY_RUN=0
UNINSTALL=0
HOOKS=1
VENV="${HOME}/.toknix/venv"
BIN_DIR="${HOME}/.local/bin"

say() { printf 'toknix: %s\n' "$*"; }
die() { printf 'toknix: error: %s\n' "$*" >&2; exit 1; }

# Run a command, or only show it with --dry-run.
run() {
    if [ "$DRY_RUN" -eq 1 ]; then
        printf '+ %s\n' "$*"
    else
        "$@"
    fi
}

while [ $# -gt 0 ]; do
    case "$1" in
        --dry-run) DRY_RUN=1 ;;
        --uninstall) UNINSTALL=1 ;;
        --no-hooks) HOOKS=0 ;;
        --from)
            [ $# -ge 2 ] || die "--from needs a source"
            SOURCE="$2"
            shift
            ;;
        -h | --help)
            sed -n '2,16p' "$0" 2>/dev/null || true
            exit 0
            ;;
        *) die "unknown option: $1 (see --help)" ;;
    esac
    shift
done

find_python() {
    for candidate in python3 python; do
        if command -v "$candidate" >/dev/null 2>&1 &&
            "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' 2>/dev/null; then
            command -v "$candidate"
            return 0
        fi
    done
    return 1
}

toknix_bin() {
    if command -v toknix >/dev/null 2>&1; then
        command -v toknix
    elif [ -x "${BIN_DIR}/toknix" ]; then
        printf '%s\n' "${BIN_DIR}/toknix"
    fi
}

uninstall() {
    tok="$(toknix_bin || true)"
    if [ -n "$tok" ]; then
        run "$tok" uninstall
    else
        say "toknix command not found; skipping hook removal"
    fi
    if command -v uv >/dev/null 2>&1 && uv tool list 2>/dev/null | grep -q '^toknix '; then
        run uv tool uninstall toknix
    fi
    if [ -d "$VENV" ]; then
        run rm -rf "$VENV"
    fi
    if [ -L "${BIN_DIR}/toknix" ]; then
        run rm -f "${BIN_DIR}/toknix"
    fi
    say "removed. Your data in ~/.toknix (stats, saved outputs) is kept; delete it by hand if you like."
}

install_with_uv() {
    say "installing with uv from ${SOURCE}"
    # --reinstall: a re-run must pick up new code even when the version number did not change
    run uv tool install --force --reinstall "$SOURCE"
}

install_with_venv() {
    py="$1"
    say "installing in ${VENV} with $("$py" --version 2>&1) from ${SOURCE}"
    if [ "$DRY_RUN" -eq 0 ] && ! "$py" -c 'import venv, ensurepip' 2>/dev/null; then
        die "this Python has no venv/ensurepip; install uv (https://docs.astral.sh/uv/) or e.g. 'sudo apt install python3-venv'"
    fi
    run "$py" -m venv "$VENV"
    run "${VENV}/bin/python" -m pip install --quiet --upgrade pip
    run "${VENV}/bin/python" -m pip install --quiet --upgrade --force-reinstall --no-deps "$SOURCE"  # same reason
    run mkdir -p "$BIN_DIR"
    run ln -sf "${VENV}/bin/toknix" "${BIN_DIR}/toknix"
}

main() {
    if [ "$UNINSTALL" -eq 1 ]; then
        uninstall
        return 0
    fi
    py="$(find_python)" || die "Python 3.9 or newer is required (python3 not found or too old)"
    if command -v uv >/dev/null 2>&1; then
        install_with_uv
    else
        install_with_venv "$py"
    fi
    tok="$(toknix_bin || true)"
    if [ "$DRY_RUN" -eq 1 ]; then
        tok="${tok:-toknix}"
    fi
    [ -n "$tok" ] || die "toknix was installed but is not on PATH; add ${BIN_DIR} to PATH and run 'toknix install'"
    case ":${PATH}:" in
        *":${BIN_DIR}:"*) ;;
        *) say "note: ${BIN_DIR} is not on PATH; add it to your shell profile" ;;
    esac
    if [ "$HOOKS" -eq 1 ]; then
        run "$tok" install
    fi
    run "$tok" doctor || true
    say "done. Restart Claude Code to load the hooks."
}

main
