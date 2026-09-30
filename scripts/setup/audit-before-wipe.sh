#!/bin/bash
# Run this on the OLD machine before wiping.
# It surfaces things that pre-wipe.sh does NOT back up,
# so you can decide if anything is worth adding.

set -eo pipefail

YELLOW='\033[0;33m'
CYAN='\033[0;36m'
DIM='\033[2m'
RESET='\033[0m'

section() { echo -e "\n${CYAN}--- $1 ---${RESET}"; }
warn()    { echo -e "  ${YELLOW}⚠${RESET} $1"; }
dim()     { echo -e "  ${DIM}$1${RESET}"; }

section "$HOME/Pictures, $HOME/Movies, $HOME/Music (not backed up)"
for dir in Pictures Movies Music; do
  if [[ -d "$HOME/$dir" ]]; then
    count=$(find "$HOME/$dir" -maxdepth 1 -not -name "$dir" -not -name '.*' 2>/dev/null | wc -l | tr -d ' ') || true
    if [[ "$count" -gt 0 ]]; then
      size=$(du -sh "$HOME/$dir" 2>/dev/null | cut -f1) || true
      warn "$HOME/$dir: $count items, $size"
    fi
  fi
done

section "$HOME/Library/LaunchAgents (custom scheduled tasks)"
if [[ -d "$HOME/Library/LaunchAgents" ]]; then
  agents=()
  for agent in "$HOME/Library/LaunchAgents/"*; do
    name=$(basename "$agent")
    if [[ -e "$agent" ]] && [[ "$name" != com.apple.* ]]; then
      agents+=("$name")
    fi
  done
  if [[ ${#agents[@]} -gt 0 ]]; then
    warn "Non-Apple launch agents found:"
    printf '    %s\n' "${agents[@]}"
  else
    dim "Only Apple defaults — nothing custom"
  fi
else
  dim "No LaunchAgents directory"
fi

section "Crontab"
cron=$(crontab -l 2>/dev/null || true)
if [[ -n "$cron" ]]; then
  warn "Active crontab entries:"
  echo "$cron" | while read -r line; do echo "    $line"; done
else
  dim "No crontab"
fi

section "Keychain (items not in a password manager)"
keychain_dump=$(security dump-keychain login.keychain-db 2>/dev/null) || true
keychain_count=$(grep -c '^keychain' <<< "$keychain_dump") || true
if [[ "$keychain_count" -gt 0 ]]; then
  warn "$keychain_count keychain entries in login.keychain-db"
  dim "Review via: Keychain Access.app → login keychain"
  dim "If you use a password manager, these are likely duplicates"
fi

section "$HOME/Library/Application Support (large app data)"
if [[ -d "$HOME/Library/Application Support" ]]; then
  echo "  Top 10 by size (excluding Brave, already synced):"
  du -sh "$HOME/Library/Application Support/"* 2>/dev/null \
    | grep -v BraveSoftware \
    | sort -rh \
    | head -10 \
    | while read -r size name; do
        echo "    $size  $(basename "$name")"
      done || true
fi

section "$HOME/.config entries not tracked by dotfiles"
if [[ -d "$HOME/.config" ]]; then
  DOTFILES="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
  for dir in "$HOME/.config/"*/; do
    name=$(basename "$dir")
    # Skip dirs that are tracked in the dotfiles repo or backed up by pre-wipe
    if [[ -e "$DOTFILES/.config/$name" ]] || \
       [[ "$name" == "AWSVPNClient" ]] || \
       [[ "$name" == "gh" ]] || \
       [[ "$name" == "graphite" ]] || \
       [[ "$name" == "acli" ]] || \
       [[ "$name" == "sourcery" ]]; then
      continue
    fi
    size=$(du -sh "$dir" 2>/dev/null | cut -f1) || true
    echo "    $size  $name"
  done
fi

section "Hidden dotfiles in ~ not tracked by dotfiles repo"
DOTFILES="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# Things pre-wipe.sh already handles
known=(.ssh .gnupg .aws .zshrc.local .zsh_history .psql_history .python_history .node_repl_history .claude)
# Things in the dotfiles repo
for f in "$DOTFILES"/.[!.]*; do
  known+=("$(basename "$f")")
done
# Common noise
known+=(.Trash .cache .cargo .rustup .npm .bun .docker .local .cups .CFUserTextEncoding .lesshst .viminfo .DS_Store .config)

for f in "$HOME"/.[!.]*; do
  name=$(basename "$f")
  skip=0
  for k in "${known[@]}"; do
    [[ "$name" == "$k" ]] && skip=1 && break
  done
  [[ "$skip" -eq 1 ]] && continue
  size=$(du -sh "$f" 2>/dev/null | cut -f1) || true
  if [[ -d "$f" ]]; then
    echo "    $size  $name/"
  else
    echo "    $size  $name"
  fi
done

section "Summary"
echo "  Review the items above. For anything you want to keep,"
echo "  either add it to pre-wipe.sh or copy it manually before wiping."
