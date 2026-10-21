#!/usr/bin/env bash
# git-crypt repository helper for credentials and obsidian vaults.
# Installed by scripts/bootstrap.sh as ~/.local/bin/git-crypt-repo; the same
# file is also symlinked as git-crypt-textconv (diff driver for encrypted
# files) and as pre-commit (guard: no commits while the repository is locked).
set -euo pipefail

BIN_DIR="$HOME/.local/bin"
HOOKS_DIR="$HOME/.local/share/git-crypt/hooks"

# --- entry by invocation name (git calls textconv/hook without subcommand) ---
self="$(basename "$0")"
if [ "$self" = "git-crypt-textconv" ]; then
    exec git-crypt smudge < "${1:?git-crypt-textconv: file argument required}"
fi
if [ "$self" = "pre-commit" ]; then
    set -- pre-commit
fi

command="${1:-}"
case "$command" in
init)
    git config core.hooksPath "$HOOKS_DIR"
    git config diff.git-crypt.textconv "$BIN_DIR/git-crypt-textconv"
    echo "init: hooks ($HOOKS_DIR) + git-crypt diff enabled"
    ;;
unlock)
    REPOSITORY_ROOT="$(git rev-parse --show-toplevel)"
    cd "$REPOSITORY_ROOT"
    # decrypted files must not leak group/other bits, whatever the umask outside
    umask 077
    chmod 700 "$REPOSITORY_ROOT"

    if [ -f .git/git-crypt/keys/default ]; then
        echo "unlock: already unlocked."
        exit 0
    fi

    KEY_BASE64="${VAULT_KEY:-}"
    if [ -z "$KEY_BASE64" ]; then
        read -r -s -p "Paste base64 git-crypt key: " KEY_BASE64
        echo
    fi

    TEMPORARY_KEY=$(mktemp)
    trap 'rm -f "$TEMPORARY_KEY"' EXIT
    printf '%s' "$KEY_BASE64" | base64 -d > "$TEMPORARY_KEY"
    [ -s "$TEMPORARY_KEY" ] || { echo "unlock: empty key." >&2; exit 1; }

    git-crypt unlock "$TEMPORARY_KEY"
    git config diff.git-crypt.textconv "$BIN_DIR/git-crypt-textconv"
    echo "unlock: repository decrypted in-place."
    ;;
lock)
    git-crypt lock
    ;;
status)
    git-crypt status
    ;;
key-show)
    cd "$(git rev-parse --show-toplevel)"
    [ -f .git/git-crypt/keys/default ] || {
        echo "key-show: repository is locked, nothing to export." >&2
        exit 1
    }
    git-crypt export-key /dev/stdout | base64
    ;;
pre-commit)
    git rev-parse --git-dir >/dev/null 2>&1 || exit 0
    if [ ! -f "$(git rev-parse --git-dir)/git-crypt/keys/default" ]; then
        echo "pre-commit: git-crypt is locked - run 'make unlock' first." >&2
        exit 1
    fi
    ;;
*)
    echo "usage: git-crypt-repo {init|unlock|lock|status|key-show}" >&2
    exit 1
    ;;
esac
