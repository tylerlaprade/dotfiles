#!/bin/sh
set -eu

configured_models='["gpt-5.6-sol","gpt-5.6-terra","gpt-5.6-luna"]'
context_window=$(
  jq -er --argjson configured_models "$configured_models" '
    [
      $configured_models[] as $model
      | [.models[]? | select(.slug == $model) | .context_window]
      | select(length == 1)
      | .[0]
    ] as $windows
    | select(($windows | length) == ($configured_models | length))
    | select(all($windows[]; type == "number" and floor == . and . >= 100000 and . <= 1000000))
    | $windows
    | min
  ' "$HOME/.codex/models_cache.json" 2>/dev/null
) || context_window=200000

unset ANTHROPIC_API_KEY
export ANTHROPIC_BASE_URL=http://127.0.0.1:8317
export ANTHROPIC_AUTH_TOKEN=claudex-local
export ANTHROPIC_CUSTOM_HEADERS="Originator: codex_cli_rs"
export ANTHROPIC_MODEL=gpt-5.6-sol
export ANTHROPIC_DEFAULT_OPUS_MODEL=gpt-5.6-sol
export ANTHROPIC_DEFAULT_SONNET_MODEL=gpt-5.6-terra
export ANTHROPIC_DEFAULT_HAIKU_MODEL=gpt-5.6-luna
export ANTHROPIC_CUSTOM_MODEL_OPTION=gpt-5.6-sol
export ANTHROPIC_CUSTOM_MODEL_OPTION_NAME="GPT-5.6 Sol"
export CLAUDE_CODE_MAX_CONTEXT_TOKENS=$context_window

exec "$@"
