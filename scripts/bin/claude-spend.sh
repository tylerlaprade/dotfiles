#!/bin/bash
# claude-spend — what local Claude Code sessions actually spent, by model.
#
# Reads ~/.claude/projects transcripts. Usage is deduplicated by message id:
# Claude Code writes one record per content block and copies the same usage
# object onto each, so summing records multiplies every multi-block turn.
# Timestamps in the transcripts are UTC; days and hours here are local.
#
# Cost is API list price, which is not what a subscription bills. It is a
# comparable weight across token kinds, not an invoice.
#
#   --since / --until   local date or datetime (default: the current Fable week)
#   --model             substring of the model id (default: fable)
#   --json              machine-readable totals

set -euo pipefail

projects="$HOME/.claude/projects"
model_filter="fable"
since_arg=""
until_arg=""
as_json=0

while [ $# -gt 0 ]; do
  case "$1" in
    --since) since_arg="$2"; shift 2 ;;
    --until) until_arg="$2"; shift 2 ;;
    --model) model_filter="$2"; shift 2 ;;
    --json)  as_json=1; shift ;;
    -h|--help) sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) printf 'unknown argument: %s\n' "$1" >&2; exit 2 ;;
  esac
done

to_epoch() { date -j -f '%Y-%m-%d %H:%M:%S' "$1" +%s 2>/dev/null || date -j -f '%Y-%m-%d' "$1" +%s 2>/dev/null; }

if [ -n "$since_arg" ]; then
  since=$(to_epoch "$since_arg") || { echo "cannot parse --since: $since_arg" >&2; exit 2; }
else
  usage_cmd=$(command -v claude-usage 2>/dev/null || command -v claude-usage.sh 2>/dev/null || true)
  resets_fable=""
  [ -n "$usage_cmd" ] && resets_fable=$("$usage_cmd" 2>/dev/null | jq -r '.resets_fable // empty' 2>/dev/null || true)
  if [ -n "$resets_fable" ]; then
    since=$(( resets_fable - 7 * 86400 ))
  else
    since=$(( $(date +%s) - 7 * 86400 ))
  fi
fi

if [ -n "$until_arg" ]; then
  until_epoch=$(to_epoch "$until_arg") || { echo "cannot parse --until: $until_arg" >&2; exit 2; }
else
  until_epoch=$(date +%s)
fi

[ "$until_epoch" -gt "$since" ] || { echo "empty window" >&2; exit 2; }

scan_days=$(( (($(date +%s) - since) / 86400) + 2 ))

rows=$(mktemp)
trap 'rm -f "$rows"' EXIT

while IFS= read -r file; do
  jq -rc --arg f "$file" --argjson lo "$since" --argjson hi "$until_epoch" --arg m "$model_filter" '
    select(.type == "assistant")
    | (.message.model // "") as $model
    | select($model | ascii_downcase | contains($m | ascii_downcase))
    | ((.timestamp // "") | sub("\\.[0-9]+Z$"; "Z")) as $ts
    | select($ts != "")
    | ($ts | fromdateiso8601) as $epoch
    | select($epoch >= $lo and $epoch <= $hi)
    | .message.usage as $u
    | [ .message.id, $epoch, ($epoch | strflocaltime("%Y-%m-%d")), $model,
        (if (.isSidechain // false) then "subagent" else "main" end),
        ($u.output_tokens // 0), ($u.input_tokens // 0),
        ($u.cache_creation.ephemeral_1h_input_tokens // 0),
        ($u.cache_creation.ephemeral_5m_input_tokens // 0),
        ($u.cache_read_input_tokens // 0), $f ] | @tsv' "$file" 2>/dev/null >> "$rows" || true
done < <(fd -e jsonl . "$projects" -t f --changed-within "${scan_days}d" 2>/dev/null)

[ -s "$rows" ] || { echo "no ${model_filter} activity in that window"; exit 0; }

awk -F'\t' -v as_json="$as_json" -v model_filter="$model_filter" \
    -v since="$since" -v until_epoch="$until_epoch" '
function rate(model) {
  if (model ~ /fable|mythos/) return 10.0
  if (model ~ /opus/)         return 5.0
  if (model ~ /sonnet-4-6/)   return 3.0
  if (model ~ /sonnet/)       return 2.0
  if (model ~ /haiku/)        return 1.0
  return 5.0
}
function dollars(out, inp, cw1h, cw5m, cr, model) {
  return (inp + out * 5 + cw1h * 2 + cw5m * 1.25 + cr * 0.1) * rate(model) / 1000000
}
function money(v) { return sprintf("$%.2f", v) }
!seen[$1]++ {
  cost = dollars($6, $7, $8, $9, $10, $4)
  calls++;  total += cost
  r = rate($4)
  out_t += $6;  out_c += $6 * 5 * r / 1000000
  in_t  += $7;  in_c  += $7 * r / 1000000
  cw_t  += $8 + $9; cw_c += ($8 * 2 + $9 * 1.25) * r / 1000000
  cr_t  += $10; cr_c  += $10 * 0.1 * r / 1000000
  day_cost[$3] += cost; day_calls[$3]++
  origin_cost[$5] += cost; origin_out[$5] += $6
  path = $11
  sub(/^.*\/projects\//, "", path)
  n = split(path, seg, "/")
  key = seg[1] " [" substr(seg[2], 1, 8) "]"
  sess_cost[key] += cost
  minute = int($2 / 60)
  if (!((key SUBSEP minute) in minute_seen)) { minute_seen[key SUBSEP minute] = 1; active_min[key]++ }
}
END {
  if (calls == 0) { print "no rows"; exit }
  if (as_json == 1) {
    printf "{\"model\":\"%s\",\"calls\":%d,\"cost_usd\":%.2f,", model_filter, calls, total
    printf "\"output_tokens\":%d,\"input_tokens\":%d,\"cache_write_tokens\":%d,\"cache_read_tokens\":%d,", out_t, in_t, cw_t, cr_t
    printf "\"main_usd\":%.2f,\"subagent_usd\":%.2f}\n", origin_cost["main"], origin_cost["subagent"]
    exit
  }
  printf "\n  %s — %d API calls, %s API-equivalent\n\n", model_filter, calls, money(total)
  printf "  where it went\n"
  printf "    %-12s %14d  %10s  %5.1f%%\n", "cache read",  cr_t,  money(cr_c),  100 * cr_c / total
  printf "    %-12s %14d  %10s  %5.1f%%\n", "cache write", cw_t,  money(cw_c),  100 * cw_c / total
  printf "    %-12s %14d  %10s  %5.1f%%\n", "output",      out_t, money(out_c), 100 * out_c / total
  printf "    %-12s %14d  %10s  %5.1f%%\n\n", "input",     in_t,  money(in_c),  100 * in_c / total
  printf "  by day\n"
  day_sort = "sort"
  for (d in day_cost)
    printf "    %s  %5d calls  %10s  %5.1f%%\n", d, day_calls[d], money(day_cost[d]), 100 * day_cost[d] / total | day_sort
  close(day_sort)
  printf "\n  main loop vs subagents\n"
  printf "    %-9s %10s  %5.1f%%\n", "main", money(origin_cost["main"]), 100 * origin_cost["main"] / total
  printf "    %-9s %10s  %5.1f%%\n", "subagent", money(origin_cost["subagent"]), 100 * origin_cost["subagent"] / total
  printf "\n  by session, with burn rate over minutes it was actually calling\n"
  sess_sort = "sort -rn | head -12"
  for (k in sess_cost) {
    span = active_min[k]; if (span < 1) span = 1
    printf "    %5.1f%%  %10s  %5d min  %8s/min  %s\n", 100 * sess_cost[k] / total, money(sess_cost[k]), span, money(sess_cost[k] / span), k | sess_sort
  }
  close(sess_sort)
  print ""
}' "$rows"
