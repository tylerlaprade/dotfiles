#!/bin/bash
# PreToolUse guard for Agent|Workflow. Agent must name a model; Workflow must
# name a model on every agent(...) call and must not reach Fable by any route,
# including a constant, and is denied when its script cannot be located. See
# the commit message for the reasoning and the scan's limits.

input=$(cat) || exit 0

# Each field gets an "x" prefix so empty fields survive read's IFS collapsing.
IFS=$'\t' read -r event tool amodel spath wname resume cwd < <(printf '%s' "$input" | jq -r \
  '[.hook_event_name // "", .tool_name // "",
    (.tool_input.model // ""), (.tool_input.scriptPath // ""),
    (.tool_input.name // ""), (.tool_input.resumeFromRunId // ""),
    .cwd // ""] | map("x" + .) | @tsv' 2>/dev/null)
event="${event#x}" tool="${tool#x}" amodel="${amodel#x}"
spath="${spath#x}" wname="${wname#x}" resume="${resume#x}" cwd="${cwd#x}"

[ "$event" = "PreToolUse" ] || exit 0

deny() {
  jq -n --arg reason "$1" '
    {hookSpecificOutput: {hookEventName: "PreToolUse",
                          permissionDecision: "deny",
                          permissionDecisionReason: $reason}}'
  exit 0
}

if [ "$tool" = "Agent" ]; then
  [ -n "$amodel" ] && exit 0
  deny "This Agent call names no model. An omitted model is inherited from the parent, not chosen — set model explicitly on the call."
fi

[ "$tool" = "Workflow" ] || exit 0

body=$(printf '%s' "$input" | jq -r '.tool_input.script // empty' 2>/dev/null)
origin="inline script"

if [ -z "$body" ] && [ -n "$spath" ]; then
  origin="scriptPath $spath"
  body=$(cat "$spath" 2>/dev/null) || body=""
  [ -n "$body" ] || deny "Workflow scriptPath could not be read ($spath), so its agent(...) models cannot be checked. Fan-out is blocked unless the script is readable."
fi

if [ -z "$body" ] && [ -n "$wname" ]; then
  for d in "${CLAUDE_PROJECT_DIR:-}" "$cwd" "$HOME"; do
    [ -n "$d" ] || continue
    if [ -f "$d/.claude/workflows/$wname.js" ]; then
      origin="named workflow $wname"
      body=$(cat "$d/.claude/workflows/$wname.js" 2>/dev/null)
      break
    fi
  done
  [ -n "$body" ] || deny "Named workflow '$wname' could not be located under .claude/workflows/, so its agent(...) models cannot be checked. Fan-out is blocked unless the script is readable."
fi

if [ -z "$body" ]; then
  if [ -n "$resume" ]; then
    deny "This Workflow resumes run $resume without naming a script, so its agent(...) models cannot be checked. Re-launch with the scriptPath so the fan-out's models are visible."
  fi
  deny "This Workflow carries no readable script, so its agent(...) models cannot be checked."
fi

IFS=$'\t' read -r total missing fable labels fablelabels fable_literals < <(printf '%s' "$body" | awk '
  { doc = doc $0 "\n" }
  END {
    rest = doc; off = 0; cnt = 0
    while (match(rest, /agent[ \t]*\(/)) {
      cnt++
      pos[cnt] = off + RSTART
      len[cnt] = RLENGTH
      off += RSTART + RLENGTH - 1
      rest = substr(rest, RSTART + RLENGTH)
    }
    missing = 0; fable = 0; labels = ""; fablelabels = ""
    for (i = 1; i <= cnt; i++) {
      st = pos[i] + len[i]
      en = (i < cnt) ? pos[i+1] - 1 : length(doc)
      s = substr(doc, st, en - st + 1)
      lab = "#" i
      if (match(s, /label[ \t]*:[ \t]*[\140\047\042][^\140\047\042]*/)) {
        lab = substr(s, RSTART, RLENGTH)
        sub(/label[ \t]*:[ \t]*[\140\047\042]/, "", lab)
      }
      ls = tolower(s)
      if (match(ls, /model[ \t]*:[ \t]*[\140\047\042][^\140\047\042]*/)) {
        m = substr(ls, RSTART, RLENGTH)
        if (m ~ /fable/) {
          fable++
          fablelabels = (fablelabels == "" ? lab : fablelabels ", " lab)
        }
      } else if (s ~ /model[ \t]*:/) {
        ;
      } else {
        missing++
        labels = (labels == "" ? lab : labels ", " lab)
      }
    }
    lit = ""; tmp = doc; low = tolower(doc)
    while (match(low, /[\047\042][^\047\042]*[\047\042]/)) {
      piece = substr(low, RSTART + 1, RLENGTH - 2)
      if (length(piece) <= 40 && piece ~ /fable/)
        lit = (lit == "" ? piece : (index(lit, piece) ? lit : lit ", " piece))
      low = substr(low, RSTART + RLENGTH)
    }
    if (labels == "") labels = "-"
    if (fablelabels == "") fablelabels = "-"
    if (lit == "") lit = "-"
    printf "%d\t%d\t%d\t%s\t%s\t%s\n", cnt, missing, fable, labels, fablelabels, lit
  }')

[ -n "$total" ] || exit 0
[ "$total" -gt 0 ] 2>/dev/null || exit 0

if [ "${fable:-0}" -gt 0 ]; then
  deny "Workflow fan-out cannot run on Fable, and these agent(...) calls name it: ${fablelabels}. Give each one a different model. ($origin)"
fi

if [ "${fable_literals:--}" != "-" ]; then
  deny "Workflow fan-out cannot run on Fable. This script names it: ${fable_literals}. A constant or lookup that resolves to Fable is still Fable — remove it from the script entirely. ($origin)"
fi

if [ "${missing:-0}" -gt 0 ]; then
  deny "These agent(...) calls name no model: ${labels}. In a fan-out an omitted model is inherited, not chosen, so every call must name one explicitly. ($origin)"
fi

exit 0
