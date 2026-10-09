typeset -U path PATH

. "$HOME/.cargo/env"
[[ -d "$HOME/.local/bin" ]] && export PATH="$HOME/.local/bin:$PATH"
[[ -d "$HOME/Code/dotfiles/scripts/bin" ]] && export PATH="$HOME/Code/dotfiles/scripts/bin:$PATH"

# Cargo has no config key for message-format (rust-lang/cargo#16371). Agent
# shells get short compiler output; editors exec cargo directly and skip this.
cargo() {
  if [[ -n $CLAUDECODE$CODEX_THREAD_ID$GROK_AGENT && $1 == (check|build|clippy|test) && $* != *--message-format* ]]; then
    command cargo $1 --message-format=short "${@:2}"
  else
    command cargo "$@"
  fi
}

# A command's output, rerun only when the file it depends on is newer than the
# saved copy. Leaves the output in REPLY. Saved per machine in TMPDIR, which a
# copied or cloned home directory never carries along.
_cached_output() {
  local cache=${TMPDIR:-/tmp/}zsh-startup-$UID/$1 dependency=$2
  shift 2
  if [[ ! -e $dependency || ! -s $cache || $dependency -nt $cache ]]; then
    if ! { mkdir -p ${cache:h} && "$@" >| $cache.$$ && mv -f $cache.$$ $cache }; then
      rm -f $cache.$$
      REPLY=
      return 1
    fi
  fi
  REPLY=$(<$cache)
}

_cached_output cpu-count /usr/sbin/sysctl /usr/sbin/sysctl -n hw.ncpu &&
  export VITEST_MAX_WORKERS=$(( REPLY / 2 ))
[[ -f "$HOME/.zshenv.local" ]] && source "$HOME/.zshenv.local"
