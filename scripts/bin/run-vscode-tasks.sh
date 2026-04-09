#!/usr/bin/env bash
# Reads .vscode/tasks.json and runs all tasks with runOn: "folderOpen".
# Resolves dependsOn ordering so dependencies run first.
# Designed to replace VS Code's auto-run behavior in terminal-based workflows.

set -euo pipefail

# Find tasks.json by walking up from cwd
find_tasks_json() {
    local dir="$PWD"
    while [[ "$dir" != "/" ]]; do
        if [[ -f "$dir/.vscode/tasks.json" ]]; then
            echo "$dir/.vscode/tasks.json"
            return 0
        fi
        dir="$(dirname "$dir")"
    done
    echo "No .vscode/tasks.json found" >&2
    return 1
}

TASKS_JSON="$(find_tasks_json)"
WORKSPACE="$(dirname "$(dirname "$TASKS_JSON")")"

# Get all folderOpen task labels
mapfile -t FOLDER_OPEN_LABELS < <(
    jq -r '.tasks[] | select(.runOptions.runOn == "folderOpen") | .label' "$TASKS_JSON"
)

if [[ ${#FOLDER_OPEN_LABELS[@]} -eq 0 ]]; then
    echo "No folderOpen tasks found"
    exit 0
fi

# Build dependency-ordered list (dependencies first, then dependents)
ordered=()
seen=()

resolve() {
    local label="$1"
    for s in "${seen[@]+"${seen[@]}"}"; do
        [[ "$s" == "$label" ]] && return 0
    done
    seen+=("$label")

    # Resolve dependencies first
    local deps
    deps="$(jq -r --arg label "$label" \
        '.tasks[] | select(.label == $label) | .dependsOn[]? // empty' "$TASKS_JSON")"
    while IFS= read -r dep; do
        [[ -n "$dep" ]] && resolve "$dep"
    done <<< "$deps"

    ordered+=("$label")
}

for label in "${FOLDER_OPEN_LABELS[@]}"; do
    resolve "$label"
done

# Run each task
for label in "${ordered[@]}"; do
    task="$(jq --arg label "$label" '.tasks[] | select(.label == $label)' "$TASKS_JSON")"
    cmd="$(echo "$task" | jq -r '.command')"
    cwd="$(echo "$task" | jq -r '.options.cwd // empty' | sed "s|\${workspaceFolder}|$WORKSPACE|g")"

    echo "==> $label"
    if [[ -n "$cwd" ]]; then
        (cd "$cwd" && eval "$cmd")
    else
        eval "$cmd"
    fi
done
