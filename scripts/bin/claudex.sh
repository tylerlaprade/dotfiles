#!/bin/sh
set -eu

exec env -u ANTHROPIC_API_KEY \
  ANTHROPIC_BASE_URL=http://127.0.0.1:8317 \
  ANTHROPIC_AUTH_TOKEN=claudex-local \
  ANTHROPIC_CUSTOM_HEADERS="Originator: codex_cli_rs" \
  ANTHROPIC_MODEL=gpt-5.6-sol \
  ANTHROPIC_DEFAULT_OPUS_MODEL=gpt-5.6-sol \
  ANTHROPIC_DEFAULT_SONNET_MODEL=gpt-5.6-sol \
  ANTHROPIC_DEFAULT_HAIKU_MODEL=gpt-5.6-sol \
  ANTHROPIC_CUSTOM_MODEL_OPTION=gpt-5.6-sol \
  ANTHROPIC_CUSTOM_MODEL_OPTION_NAME="GPT-5.6 Sol" \
  claude --settings '{"ultracode": true}' "$@"
