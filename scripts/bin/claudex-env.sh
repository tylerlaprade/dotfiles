#!/bin/sh
set -eu

configured_models='["gpt-6-astra","gpt-5.6-sol","gpt-5.6-terra","gpt-5.6-luna"]'
context_window=$(
  jq -er --argjson configured_models "$configured_models" '
    [
      $configured_models[] as $model
      | [.models[]? | select(.slug == $model) | (.max_context_window // .context_window)]
      | select(length == 1)
      | .[0]
    ] as $windows
    | select(($windows | length) == ($configured_models | length))
    | select(all($windows[]; type == "number" and floor == . and . >= 100000 and . <= 1000000))
    | $windows
    | min
  ' "$HOME/.codex/models_cache.json" 2>/dev/null
) || context_window=272000

unset ANTHROPIC_API_KEY
export ANTHROPIC_BASE_URL=http://127.0.0.1:8317
export ANTHROPIC_AUTH_TOKEN=claudex-local
export ANTHROPIC_CUSTOM_HEADERS="Originator: codex_cli_rs"
export ANTHROPIC_MODEL=gpt-6-astra
export ANTHROPIC_DEFAULT_FABLE_MODEL=gpt-6-astra
export ANTHROPIC_DEFAULT_OPUS_MODEL=gpt-5.6-sol
export ANTHROPIC_DEFAULT_SONNET_MODEL=gpt-5.6-terra
export ANTHROPIC_DEFAULT_HAIKU_MODEL=gpt-5.6-luna
export ANTHROPIC_CUSTOM_MODEL_OPTION=gpt-6-astra
export ANTHROPIC_CUSTOM_MODEL_OPTION_NAME="GPT-6 Astra"
export CLAUDE_CODE_MAX_CONTEXT_TOKENS="$context_window"
export CLAUDE_CODE_AUTO_COMPACT_WINDOW=$((context_window * 95 / 100))

exec "$@"
