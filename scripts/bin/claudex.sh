#!/bin/sh
set -eu

launcher_path=$0
while [ -L "$launcher_path" ]; do
  launcher_dir=$(CDPATH='' cd -P "$(dirname "$launcher_path")" && pwd)
  launcher_path=$(readlink "$launcher_path")
  case $launcher_path in
    /*) ;;
    *) launcher_path=$launcher_dir/$launcher_path ;;
  esac
done
launcher_dir=$(CDPATH='' cd -P "$(dirname "$launcher_path")" && pwd)
env_wrapper=$launcher_dir/claudex-env.sh
settings=$(jq -cn --arg process_wrapper "$env_wrapper" '
  {
    processWrapper: $process_wrapper,
    feedbackDrafts: "off",
    env: {
      CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS: "",
      ANTHROPIC_MODEL: "gpt-6-astra",
      ANTHROPIC_DEFAULT_FABLE_MODEL: "gpt-6-astra",
      ANTHROPIC_DEFAULT_OPUS_MODEL: "gpt-5.6-sol",
      ANTHROPIC_DEFAULT_SONNET_MODEL: "gpt-5.6-terra",
      ANTHROPIC_DEFAULT_HAIKU_MODEL: "gpt-5.6-luna"
    },
    permissions: {
      deny: ["ListAgents", "SendMessage"]
    },
    modelPicker: {
      replaceBuiltInOptions: true,
      options: [
        {model: "gpt-6-astra", label: "GPT-6 Astra"},
        {model: "gpt-5.6-sol", label: "GPT-5.6 Sol"},
        {model: "gpt-5.6-terra", label: "GPT-5.6 Terra"},
        {model: "gpt-5.6-luna", label: "GPT-5.6 Luna"}
      ]
    }
  }')

exec "$env_wrapper" claude --settings "$settings" "$@"
