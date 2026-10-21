# Prompt: oh-my-posh with custom theme
eval "$(oh-my-posh init zsh --config "$HOME/.config/zenwritten_dark/zenwritten_dark.omp.json")"

# Homebrew prefix (single lookup for plugin/completion paths below)
BREW_PREFIX="$(brew --prefix)"

# zsh-completions: community definitions (must land in fpath before compinit)
fpath=("$BREW_PREFIX/share/zsh-completions" $fpath)

# Completions (must come before plugins that hook into ZLE)
autoload -Uz compinit && compinit

# Plugins
source "$BREW_PREFIX/share/zsh-autosuggestions/zsh-autosuggestions.zsh"
source "$BREW_PREFIX/share/zsh-syntax-highlighting/zsh-syntax-highlighting.zsh"

# autosuggestions: history + completion fallback
ZSH_AUTOSUGGEST_STRATEGY=(history completion)
ZSH_AUTOSUGGEST_HIGHLIGHT_STYLE='fg=8'

# nkp (NeoKeePass): default vault path (so `nkp list`/`show`/`add` need no <file>)
export NKP_VAULT="$HOME/credentials/nkp"

# pi: контейнерный агент (compose: pi-agent + herdr-bridge; docker — всегда,
# через builder-VM pi-builder без privileged). --unsafe — хостовый pi.
# Доктрина same-path (корни: ~/.config/pi/pi-mounts — вне агенто-доступных
# путей): cwd запуска обязан быть
# внутри смонтированного корня — иначе контейнеру будет виден весь каталог.
pi() {
  "$HOME/pi-sandbox/bin/pi-run" "$@"
}

# ssh wrapper for knock-protected hosts: private overlay from ~/credentials
# (knock sequences, host topology, VPN-bind logic) — sourced as a function.
[ -f "$HOME/credentials/ssh/wrapper.zsh" ] && source "$HOME/credentials/ssh/wrapper.zsh"

# SSH agent: preload keys from the macOS keychain (silent, idempotent).
# - the zshrc hook below lifts every key whose passphrase is already stored
#   in the keychain (passphrase-less keys are read straight from IdentityFile,
#   they don't need the agent)
# - ssh-keychain-add: one-time import of ALL ~/.ssh keys into keychain+agent
#   (asks the passphrase once per protected key, then it is stored forever)
ssh-keychain-add() {
  local k n=0
  for k in ~/.ssh/*(N); do
    [[ -f $k ]] || continue
    head -1 "$k" 2>/dev/null | grep -q "PRIVATE KEY" || continue
    ssh-add --apple-use-keychain "$k" && n=$((n+1))
  done
  echo "ssh-keychain-add: $n key(s) now in agent"
  ssh-add -l 2>/dev/null
}
if [[ "$(uname -s)" == Darwin && -n "$SSH_AUTH_SOCK" ]] && ! ssh-add -l &>/dev/null; then
  ssh-add -A 2>/dev/null
fi
