#!/usr/bin/env zsh
# Run VSCode tasks.json tasks that have runOn: folderOpen
set -e

ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"

typeset -A pids

run_task() {
    local label="$1"
    local task=$(jq -c ".tasks[] | select(.label == \"$label\")" .vscode/tasks.json)
    local cmd=$(echo "$task" | jq -r '.command')
    local cwd=$(echo "$task" | jq -r '.options.cwd // ""' | sed "s|\${workspaceFolder}|$ROOT|g")

    echo "==> $label"
    if [[ -n "$cwd" ]]; then
        (cd "$cwd" && eval "$cmd")
    else
        eval "$cmd"
    fi
}

# Start all tasks, waiting for dependencies first
while IFS= read -r label; do
    # Wait for dependencies
    while IFS= read -r dep; do
        [[ -n "$dep" && -n "${pids[$dep]}" ]] && wait "${pids[$dep]}"
    done < <(jq -r ".tasks[] | select(.label == \"$label\") | .dependsOn // [] | .[]" .vscode/tasks.json)

    run_task "$label" &
    pids["$label"]=$!
done < <(jq -r '.tasks[] | select(.runOptions.runOn == "folderOpen") | .label' .vscode/tasks.json)

wait
echo "All startup tasks complete."
