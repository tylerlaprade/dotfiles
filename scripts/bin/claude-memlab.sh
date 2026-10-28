#!/bin/sh
set -eu

lab_dir=$HOME/.local/state/claude-memlab
live_dir=$HOME/.claude
live_state=$HOME/.claude.json
lab_state=$lab_dir/.claude.json

mkdir -p "$lab_dir"
chmod 700 "$lab_dir"

for shared in CLAUDE.md settings.json statusline.sh keybindings.json hooks rules skills agents commands plugins; do
  if [ -e "$live_dir/$shared" ]; then
    ln -sfn "$live_dir/$shared" "$lab_dir/$shared"
  fi
done

if [ ! -f "$lab_state" ]; then
  cp "$live_state" "$lab_state"
  chmod 600 "$lab_state"
fi

next_state=$(mktemp "$lab_dir/.claude.json.XXXXXX")
jq --slurpfile live "$live_state" '
  .oauthAccount = $live[0].oauthAccount
  | .cachedGrowthBookFeatures = ($live[0].cachedGrowthBookFeatures + {
      tengu_moth_copse: true,
      tengu_passport_quail: true,
      tengu_slate_thimble: true,
      tengu_onyx_plover: (($live[0].cachedGrowthBookFeatures.tengu_onyx_plover // {}) + {enabled: true})
    })
' "$lab_state" > "$next_state"
mv "$next_state" "$lab_state"

export CLAUDE_CONFIG_DIR="$lab_dir"
# An empty value keeps the normal login's keychain entry instead of one keyed to this folder.
export CLAUDE_SECURESTORAGE_CONFIG_DIR=
# Claude Code reads the edited flag cache only with telemetry off; with it on, the server's flags replace the cache.
export DISABLE_TELEMETRY=1
export CLAUDE_CODE_GB_DISK_CACHE_WHEN_TELEMETRY_OFF=1
# Limbic stays off here so the lab shows Anthropic's memory alone.
exec claude --settings '{"enabledPlugins": {"limbic@limbic": false}}' "$@"
