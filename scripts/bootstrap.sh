#!/usr/bin/env bash
# Bootstrap $HOME: symlinks, external repos, brew packages.
# Safe to re-run: symlinks are overwritten, repos are pulled, brew bundle is idempotent.
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOCAL_BIN="$HOME/.local/bin"

link() {
  local src="$DIR/$1" dst="$HOME/$2"
  ln -sfn "$src" "$dst"
  echo "linked:  $dst -> $src"
}

# Commands from scripts/ onto $LOCAL_BIN (names without .sh: wt, herdr-tab...).
# git-crypt-textconv is a second name of git-crypt-repo (dispatch by $0).
install_command() {
  local src="$1" name="$2"
  ln -sfn "$src" "$LOCAL_BIN/$name"
  echo "command: $LOCAL_BIN/$name -> $src"
}

clone_or_pull() {
  local repo="$1" dest="$2" tmp
  if [ -d "$dest/.git" ]; then
    git -C "$dest" pull --ff-only
    echo "updated: $dest"
  elif [ -e "$dest" ] && [ -n "$(ls -A "$dest" 2>/dev/null)" ]; then
    tmp="$(mktemp -d)"
    git clone "$repo" "$tmp/src"
    cp -a "$tmp/src/." "$dest/"
    rm -rf "$tmp"
    echo "merged-clone: $dest"
  else
    mkdir -p "$(dirname "$dest")"
    git clone "$repo" "$dest"
    echo "cloned:  $dest"
  fi
}

# git-crypt repository: unlock with the key from $KEY_VAR (prompt when the
# repository is locked and the variable is unset; no-op when already unlocked).
unlock_repo() {
  local repo="$1" key_var="$2" key_value
  key_value="$(printenv "$key_var" 2>/dev/null || true)"
  if [ ! -f "$repo/.git/git-crypt/keys/default" ] && [ -z "$key_value" ]; then
    read -r -s -p "$key_var (base64, from password manager): " key_value || {
      echo "unlock_repo: $key_var required (stdin is not a terminal)" >&2
      return 1
    }
    echo
  fi
  make -C "$repo" init
  VAULT_KEY="$key_value" make -C "$repo" unlock
}

# Register a directory in Obsidian's vault list so it shows up on launch.
register_vault() {
  python3 - "$1" <<'PY'
import json, sys, time, uuid, pathlib
vault = sys.argv[1]
p = pathlib.Path.home() / "Library/Application Support/obsidian/obsidian.json"
p.parent.mkdir(parents=True, exist_ok=True)
data = json.loads(p.read_text()) if p.exists() else {"vaults": {}}
if not any(v.get("path") == vault for v in data.get("vaults", {}).values()):
    data.setdefault("vaults", {})[uuid.uuid4().hex[:16]] = {
        "path": vault, "ts": int(time.time() * 1000), "open": True
    }
    p.write_text(json.dumps(data, indent=2))
    print(f"registered: {vault}")
else:
    print(f"already registered: {vault}")
PY
}

# --- Xcode Command Line Tools (git; hard dependency of brew) ---
# Opens a GUI installer — user clicks through, script waits for it to finish.
if ! xcode-select -p >/dev/null 2>&1; then
    echo "CLT: missing — installer opened, click through (waiting)…"
    xcode-select --install >/dev/null 2>&1 || true
    WAITED=0
    until xcode-select -p >/dev/null 2>&1; do
        sleep 5
        WAITED=$((WAITED + 5))
        if [ "$WAITED" -ge 900 ]; then
            echo "CLT: not installed after 15 min — rerun bootstrap.sh" >&2
            exit 1
        fi
    done
fi

# --- Homebrew ---
if ! command -v brew >/dev/null 2>&1; then
    echo "brew: installing…"
    NONINTERACTIVE=1 /bin/bash -c \
      "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    # shellenv makes brew visible to this same run (fresh install, new shell session)
    for BREW_BIN in /opt/homebrew/bin/brew /usr/local/bin/brew; do
        [ -x "$BREW_BIN" ] && eval "$($BREW_BIN shellenv)" && break
    done
fi

echo "applying Brewfile…"
# Homebrew refuses formulae/casks from non-official taps until trusted;
# trust state is per-machine (~/.homebrew/trust.json) — redo on each new machine
for tap in hashicorp/tap int128/kubelogin jandedobbeleer/oh-my-posh nikitabobko/tap; do
    brew trust "$tap" >/dev/null 2>&1 || true   # old brew without `trust` — skip
done
brew bundle --file "$DIR/Brewfile"
# some formula postinstalls leave /opt/homebrew/share group-writable, which makes
# zsh compinit flag it as insecure and silently drop ALL completions
chmod g-w "$(brew --prefix)/share" 2>/dev/null || true

# --- gh auth: required before cloning private repos below ---
if ! gh auth status >/dev/null 2>&1; then
    echo "gh: not authenticated — browser login (protocol: HTTPS)"
    gh auth login --git-protocol https
fi

if [ ! -d "$HOME/.pi/agent/.git" ]; then
    git clone https://github.com/s3nn1k/pi-agent.git "$HOME/.pi/agent"
fi
# grafana MCP (uvx mcp-grafana): прогрев кэша uv — иначе первая pi-сессия ждёт ~16 MB скачивания
uvx mcp-grafana --help >/dev/null 2>&1 || echo "warn: mcp-grafana prewarm failed"

clone_or_pull https://github.com/s3nn1k/pi-sandbox.git "$HOME/pi-sandbox"

bash "$HOME/pi-sandbox/install.sh"

# --- commands from scripts/ (before repos below: their make targets use them) ---
mkdir -p "$LOCAL_BIN"
install_command "$DIR/scripts/wt.sh" wt
install_command "$DIR/scripts/herdr-tab.sh" herdr-tab
install_command "$DIR/scripts/git-crypt-repo.sh" git-crypt-repo
install_command "$DIR/scripts/git-crypt-repo.sh" git-crypt-textconv
install_command "$DIR/scripts/credentials-setup.sh" credentials-setup
# shared pre-commit guard for all git-crypt repositories (core.hooksPath target)
mkdir -p "$HOME/.local/share/git-crypt/hooks"
ln -sfn "$LOCAL_BIN/git-crypt-repo" "$HOME/.local/share/git-crypt/hooks/pre-commit"

# --- file symlinks ---
link gitconfig       .gitconfig
link aerospace.toml  .aerospace.toml
link zshrc           .zshrc
link zprofile        .zprofile

# herdr: terminal workspace manager (config file only; runtime state —
# сокеты/логи — остаётся в обычном ~/.config/herdr)
mkdir -p "$HOME/.config/herdr"
link herdr.toml .config/herdr/config.toml
# pi-интеграция: состояния агентов для сайдбара/уведомлений (ставит актуальную версию)
herdr integration install pi >/dev/null 2>&1 || true
# плагин ws-keys: клавиша ряда q..p в строках воркспейсов ($key в сайдбаре)
herdr plugin link "$DIR/herdr-plugins/ws-keys" >/dev/null 2>&1 || true

  # herdr-барьер для контейнерного pi (сайдбар herdr): TCP(loopback)->unix.
# Доступ из VM1 — через gvproxy NAT (loopback мака).
launchctl bootout "gui/$(id -u)/com.s3nn1k.herdr-bridge" >/dev/null 2>&1 || true
launchctl bootout "gui/$(id -u)/com.pi-sandbox.herdr-bridge" >/dev/null 2>&1 || true
sed "s|__HOME__|$HOME|" "$HOME/pi-sandbox/launchagents/com.pi-sandbox.herdr-bridge.plist" \
  > "$HOME/Library/LaunchAgents/com.pi-sandbox.herdr-bridge.plist"
launchctl bootstrap "gui/$(id -u)" \
  "$HOME/Library/LaunchAgents/com.pi-sandbox.herdr-bridge.plist" && echo "herdr-bridge: agent loaded"

# iTerm2 Dynamic Profile (auto-reloads, JSON format)
ITERM_DP="$HOME/Library/Application Support/iTerm2/DynamicProfiles"
mkdir -p "$ITERM_DP"
ln -sfn "$DIR/iterm2.json" "$ITERM_DP/dotfiles.json"
echo "linked:  $ITERM_DP/dotfiles.json"
# Set "Dotfiles" as the default profile (GUID must match the JSON)
defaults write com.googlecode.iterm2 "Default Bookmark Guid" \
  -string "8F407364-0CD1-4EE5-8861-6C3FACFCC48E"
echo "iterm2:  default profile set to 'Dotfiles'"

# --- macOS: раскладки и переключение ввода ---
# ABC + Русская-ПК (RussianWin); переключение раскладки — Ctrl+Space
# (id=61 next source), дубль на Ctrl+Opt+Space (id=60) выключен —
# ctrl+b остаётся свободным под herdr-префикс.
# Применяется после logout/reboot; команды идемпотентны.
defaults write com.apple.HIToolbox AppleEnabledInputSources -array \
  '{InputSourceKind = "Keyboard Layout"; "KeyboardLayout ID" = 252; "KeyboardLayout Name" = ABC;}' \
  '{InputSourceKind = "Keyboard Layout"; "KeyboardLayout ID" = 19458; "KeyboardLayout Name" = RussianWin;}'
defaults write com.apple.symbolichotkeys AppleSymbolicHotKeys -dict-add \
  60 '{enabled = 0; value = {parameters = (32, 49, 786432); type = standard;};}' \
  61 '{enabled = 1; value = {parameters = (32, 49, 262144); type = standard;};}'

# --- external repos ---
clone_or_pull https://github.com/s3nn1k/nvim.git            "$HOME/.config/nvim"
clone_or_pull https://github.com/s3nn1k/zenwritten_dark.git "$HOME/.config/zenwritten_dark"

# Run nvim's own bootstrap if provided
if [ -x "$HOME/.config/nvim/init.sh" ]; then
  echo "running: ~/.config/nvim/init.sh"
  "$HOME/.config/nvim/init.sh"
fi

# --- machine credentials: git-crypt repository, secrets linked into $HOME ---
CREDENTIALS="$HOME/credentials"
clone_or_pull https://github.com/s3nn1k/credentials.git "$CREDENTIALS"
unlock_repo "$CREDENTIALS" CREDENTIALS_KEY
make -C "$CREDENTIALS" link

# --- Obsidian: note vaults + shared config (.obsidian) ---
clone_or_pull https://github.com/s3nn1k/obsidian-config.git "$HOME/.config/obsidian"

for vault_name in personal work; do
  VAULT="$HOME/vaults/$vault_name"
  mkdir -p "$(dirname "$VAULT")"
  clone_or_pull "https://github.com/s3nn1k/obsidian-$vault_name.git" "$VAULT"
  case "$vault_name" in
    personal) unlock_repo "$VAULT" PERSONAL_KEY ;;
    work)     unlock_repo "$VAULT" WORK_KEY ;;
  esac
  ln -sfn "$HOME/.config/obsidian" "$VAULT/.obsidian"
  echo "linked:  $VAULT/.obsidian -> ~/.config/obsidian"
  register_vault "$VAULT"
done

# --- NeoKeePass (nkp): encrypted password vault tool ---
NKP="$HOME/projects/personal/nkp"
clone_or_pull https://github.com/s3nn1k/nkp.git "$NKP"
make -C "$NKP" install
NKP_VAULT="$HOME/credentials/nkp" "$HOME/.local/bin/nkp" info main >/dev/null \
  || echo "warn: nkp vault check failed (locked or no 'main' base?)"

# --- whisper.cpp: large-v3-turbo model (~1.6 GB, skip if already there) ---
# Binary comes from the Brewfile (brew whisper-cpp); it only reads WAV 16 kHz,
# the `wt` zsh function converts everything else via ffmpeg.
WMODEL="$HOME/.local/share/whisper/models/ggml-large-v3-turbo.bin"
mkdir -p "$(dirname "$WMODEL")"
if [ ! -s "$WMODEL" ]; then
    echo "downloading: whisper large-v3-turbo (~1.6 GB)"
    curl -L -C - -o "$WMODEL" \
      https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3-turbo.bin
fi

# --- ручные шаги (GUI/секреты, не автоматизируются) ---
echo "manual steps:
  1. auth.json -> ~/.pi/agent/auth.json (секрет; руками с прежней машины/менеджера паролей)
  2. Happ: логин (TUN-режим и обход локальных сетей — см. ~/credentials/README.md)
  3. AirPlay-ресивер OFF: System Settings -> General -> AirDrop & Handoff (порты 5000/7000)
  4. токены агента: ~/credentials/agents-credentials/git-credentials (git-credential-store)
  5. mcp.json -> ~/.pi/agent/mcp.json (grafana-mcp; токен сервис-аккаунта из Grafana)"
