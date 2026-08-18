#!/bin/bash
# claude-usage — print this claude.ai subscription's usage as JSON.
#
# Uses the Claude Code login (Keychain item "Claude Code-credentials"), not
# an API key and not usage-credit billing. GET /api/oauth/usage is the same
# endpoint Claude Code uses for /usage.
#
# Prints: ok, five_hour, seven_day, fable, resets_5h, resets_7d, resets_fable,
# updated_at, and error when ok is false. Percents are 0-100. Resets are unix
# seconds. fable is the weekly Fable cap (limits[] weekly_scoped).
#
# A failed fetch still prints the last cache, with ok=false, and exits 1 so
# resume does not treat stale numbers as live. Statusline can show them dimmed.
#
# --fresh  ignore the 60s cache
# Cached at /tmp/claude-usage.json so statusline and resume share one fetch.

set -euo pipefail

cache=/tmp/claude-usage.json
fresh=0
[ "${1:-}" = --fresh ] && fresh=1

now=$(date +%s)

emit_stale() {
  local err=$1
  if [ -f "$cache" ]; then
    jq -c --arg err "$err" '. + {ok: false, error: $err}' "$cache"
  else
    printf '%s\n' "{\"ok\":false,\"error\":$(printf '%s' "$err" | jq -Rs .)}"
  fi
  exit 1
}

if [ "$fresh" -eq 0 ] && [ -f "$cache" ]; then
  cached_at=$(jq -r '.updated_at // 0' "$cache" 2>/dev/null || echo 0)
  cached_ok=$(jq -r '.ok // true' "$cache" 2>/dev/null || echo true)
  if [ "$cached_ok" = true ] && [ "$cached_at" -ge $(( now - 60 )) ]; then
    cat "$cache"
    exit 0
  fi
fi

blob=$(security find-generic-password -s "Claude Code-credentials" -w 2>/dev/null || true)
if [ -z "$blob" ] && [ -f "${HOME}/.claude/.credentials.json" ]; then
  blob=$(cat "${HOME}/.claude/.credentials.json")
fi
if [ -z "$blob" ]; then
  echo "claude-usage: no Claude Code login (Keychain item Claude Code-credentials)" >&2
  emit_stale "no login"
fi

# expiresAt is milliseconds. A missing/zero expiry is treated as usable;
# the request will 401 if the token is dead.
eval "$(printf '%s' "$blob" | jq -r '
  .claudeAiOauth // empty
  | "token=\(.accessToken | @sh)",
    "exp_ms=\(.expiresAt // 0)"
')"
if [ -z "${token:-}" ] || [ "$token" = "null" ]; then
  echo "claude-usage: Claude Code login has no access token" >&2
  emit_stale "no token"
fi
if [ "${exp_ms:-0}" -gt 1000000000000 ]; then
  exp_s=$(( exp_ms / 1000 ))
  if [ "$exp_s" -le "$now" ]; then
    echo "claude-usage: Claude Code access token is expired — open claude once to refresh" >&2
    emit_stale "token expired"
  fi
fi

tmp="${cache}.next"
code=$(curl -sS -o "$tmp" -w '%{http_code}' \
  --max-time 8 \
  -H "Authorization: Bearer ${token}" \
  -H "Content-Type: application/json" \
  -H "anthropic-beta: oauth-2025-04-20" \
  https://api.anthropic.com/api/oauth/usage || true)
# Drop the token from this shell as soon as the request is done.
token=

if [ "$code" != "200" ] || [ ! -s "$tmp" ]; then
  rm -f "$tmp"
  echo "claude-usage: GET /api/oauth/usage failed (HTTP ${code:-000})" >&2
  emit_stale "HTTP ${code:-000}"
fi

jq -c '
  def ts:
    if . == null or . == "" then 0
    else (sub("\\.[0-9]+"; "") | sub("\\+00:00$"; "Z") | fromdateiso8601)
    end;
  def pct:
    (. // 0 | if type == "number" then floor else 0 end);
  . as $r
  | ($r.limits // []
      | map(select(
          .kind == "weekly_scoped"
          and ((.scope.model.display_name // "") | ascii_downcase) == "fable"
        ))
      | .[0]) as $f
  | {
      five_hour: ($r.five_hour.utilization | pct),
      seven_day: ($r.seven_day.utilization | pct),
      fable: (($f.percent // 0) | pct),
      resets_5h: ($r.five_hour.resets_at | ts),
      resets_7d: ($r.seven_day.resets_at | ts),
      resets_fable: (($f.resets_at // $r.seven_day.resets_at) | ts),
      updated_at: now | floor,
      ok: true
    }
' "$tmp" | tee "$cache"
rm -f "$tmp"
