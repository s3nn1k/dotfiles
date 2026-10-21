# Editor: nvim для внешнего редактора (pi ctrl+g, git, herdr) — иначе macOS падает в nano
export EDITOR=nvim
export VISUAL=nvim

# Initialize Homebrew on login (sets PATH, MANPATH, etc.)
if [ -x /opt/homebrew/bin/brew ]; then
  eval "$(/opt/homebrew/bin/brew shellenv)"
  # psql client (brew libpq is keg-only — not linked into /opt/homebrew/bin)
  export PATH="/opt/homebrew/opt/libpq/bin:$PATH"
fi

# ~/.local/bin on PATH (pipx tools, nkp, etc. live here)
export PATH="$HOME/.local/bin:$PATH"

# Go binaries (guarded: go may be absent on a fresh machine until brew bundle runs)
command -v go >/dev/null 2>&1 && export PATH="$PATH:$(go env GOPATH)/bin"
