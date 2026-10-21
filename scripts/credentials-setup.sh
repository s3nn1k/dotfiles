#!/usr/bin/env bash
# ~/credentials wiring: link (symlink secrets into $HOME) and secure (tighten
# permissions of the decrypted tree). The repository itself is cloned by
# scripts/bootstrap.sh; this helper only operates on the standard path.
set -euo pipefail

REPOSITORY_ROOT="$HOME/credentials"
[ -d "$REPOSITORY_ROOT" ] || {
    echo "credentials-setup: $REPOSITORY_ROOT not found (bootstrap first)." >&2
    exit 1
}

link_file() {
    local source_path="$1" destination_path="$2"
    [ -e "$source_path" ] || { echo "link: source missing, skipping: $source_path"; return; }
    mkdir -p "$(dirname "$destination_path")"
    ln -sfn "$source_path" "$destination_path"
    echo "linked: $destination_path -> $source_path"
}

secure() {
    chmod 700 "$REPOSITORY_ROOT"

    for directory_path in \
        "$REPOSITORY_ROOT/agents-credentials" \
        "$REPOSITORY_ROOT/git" \
        "$REPOSITORY_ROOT/kube" \
        "$REPOSITORY_ROOT/nkp" \
        "$REPOSITORY_ROOT/ssh"; do
        [ -d "$directory_path" ] || continue
        chmod 700 "$directory_path"
    done

    for file_path in \
        "$REPOSITORY_ROOT"/git/* \
        "$REPOSITORY_ROOT/agents-credentials/git-credentials" \
        "$REPOSITORY_ROOT"/kube/* \
        "$REPOSITORY_ROOT"/nkp/*.txt \
        "$REPOSITORY_ROOT"/ssh/*; do
        [ -f "$file_path" ] || continue
        chmod 600 "$file_path"
    done
}

command="${1:-}"
case "$command" in
secure)
    secure
    ;;
link)
    # secrets go live through symlinks; tighten permissions before exposing them
    secure

    if [ -d "$REPOSITORY_ROOT/ssh" ]; then
        mkdir -p "$HOME/.ssh" && chmod 700 "$HOME/.ssh"
        for source_path in "$REPOSITORY_ROOT"/ssh/*; do
            [ -e "$source_path" ] || continue
            # *.zsh (ssh wrapper function) is sourced by zshrc, not linked here
            case "$source_path" in *.zsh) continue ;; esac
            link_file "$source_path" "$HOME/.ssh/$(basename "$source_path")"
        done
    fi

    if [ -d "$REPOSITORY_ROOT/kube" ]; then
        mkdir -p "$HOME/.kube" && chmod 700 "$HOME/.kube"
        for source_path in "$REPOSITORY_ROOT"/kube/*; do
            [ -e "$source_path" ] || continue
            link_file "$source_path" "$HOME/.kube/$(basename "$source_path")"
        done
    fi

    if [ -f "$REPOSITORY_ROOT/git/git-credentials" ]; then
        link_file "$REPOSITORY_ROOT/git/git-credentials" "$HOME/.git-credentials"
    fi
    ;;
*)
    echo "usage: credentials-setup {link|secure}" >&2
    exit 1
    ;;
esac
