#!/bin/sh
set -eu

launcher_path=$0
while [ -L "$launcher_path" ]; do
  launcher_dir=$(CDPATH= cd -P "$(dirname "$launcher_path")" && pwd)
  launcher_path=$(readlink "$launcher_path")
  case $launcher_path in
    /*) ;;
    *) launcher_path=$launcher_dir/$launcher_path ;;
  esac
done
launcher_dir=$(CDPATH= cd -P "$(dirname "$launcher_path")" && pwd)
env_wrapper=$launcher_dir/claudex-env.sh
settings=$(jq -cn --arg process_wrapper "$env_wrapper" \
  '{ultracode: true, processWrapper: $process_wrapper}')

exec "$env_wrapper" claude --settings "$settings" "$@"
