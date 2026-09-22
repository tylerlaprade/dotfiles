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
# --fresh  ignore the cache (resume)
# --async  print cache now; refresh in a detached process if stale (statusline)
# Cached at /tmp/claude-usage.json so statusline and resume share one fetch.

set -euo pipefail

cache=/tmp/claude-usage.json
fresh=0
async=0
case "${1:-}" in
  --fresh) fresh=1 ;;
  --async) async=1 ;;
esac

now=$(date +%s)

lock=/tmp/claude-usage.fetch

_claim_fetch_lock() {
  if mkdir "$lock" 2>/dev/null; then
    return 0
  fi
  # A killed fetch leaves the dir behind. Curl's timeout is 8s; a lock
  # older than a minute is leftover, not in-flight.
  local stamp
  stamp=$(stat -f %m "$lock" 2>/dev/null) || return 1
  [ $(( now - stamp )) -gt 60 ] || return 1
  rmdir "$lock" 2>/dev/null || true
  mkdir "$lock" 2>/dev/null || return 1
}

_spawn_refresh() {
  # Close inherited fds first so a caller in $(...) does not wait on us.
  (
    exec >/dev/null 2>&1 </dev/null
    "$0" || true
    rmdir "$lock" 2>/dev/null || true
  ) &
  disown 2>/dev/null || true
}

emit_stale() {
  local err=$1
  local failed_cache
  failed_cache=$(mktemp "${cache}.XXXXXX")
  if [ -f "$cache" ]; then
    jq -c --arg err "$err" --argjson now "$now" \
      '. + {ok: false, error: $err, fetched_at: $now}' "$cache" > "$failed_cache"
  else
    printf '%s\n' "{\"ok\":false,\"error\":$(printf '%s' "$err" | jq -Rs .),\"fetched_at\":${now}}" > "$failed_cache"
  fi
  mv "$failed_cache" "$cache"
  cat "$cache"
  exit 1
}

# Cache windows. Fable moves only when a /v1/messages call touches this
# account, so we can gate refreshes on statusline-observed 5h/7d activity
# instead of polling on a fixed clock. The floor keeps recent cache warm; the
# heartbeat catches usage from other machines that this session never sees.
CACHE_FLOOR=300
CACHE_HEARTBEAT=1800

# Activity: stdin 5h or 7d passed by the statusline exceeds what the cache
# last recorded, so the account has burned budget since the last successful
# fetch and Fable may have moved.
detect_activity() {
  [ ! -f "$cache" ] && return 0
  local cache_5h cache_7d
  cache_5h=$(jq -r '.five_hour // -1' "$cache" 2>/dev/null || echo -1)
  cache_7d=$(jq -r '.seven_day // -1' "$cache" 2>/dev/null || echo -1)
  [ -n "${STATUSLINE_5H:-}" ] && [ "$STATUSLINE_5H" -gt "$cache_5h" ] && return 0
  [ -n "${STATUSLINE_7D:-}" ] && [ "$STATUSLINE_7D" -gt "$cache_7d" ] && return 0
  return 1
}

# Serve cache when it is younger than the floor, or when it is younger than
# the heartbeat AND nothing has happened since it was written. A cached 429
# stays until the floor expires so we do not hammer during a throttle.
should_serve_cache() {
  [ ! -f "$cache" ] && return 1
  local age err
  age=$(( now - $(jq -r '.fetched_at // .updated_at // 0' "$cache" 2>/dev/null || echo 0) ))
  [ "$age" -lt "$CACHE_FLOOR" ] && return 0
  err=$(jq -r '.error // ""' "$cache" 2>/dev/null || echo "")
  [ -n "$err" ] && return 1
  [ "$age" -ge "$CACHE_HEARTBEAT" ] && return 1
  detect_activity && return 1
  return 0
}

if [ "$async" -eq 1 ]; then
  [ -f "$cache" ] && cat "$cache"
  if ! should_serve_cache && _claim_fetch_lock; then
    _spawn_refresh
  fi
  exit 0
fi

if [ "$fresh" -eq 0 ] && should_serve_cache; then
  cat "$cache"
  [ "$(jq -r '.ok != false' "$cache" 2>/dev/null)" = true ]
  exit $?
fi

script_path=$(realpath "${BASH_SOURCE[0]}")
keychain_check="$(dirname "$script_path")/../keychain-unlocked.py"
python3 "$keychain_check" 2>/dev/null || emit_stale "keychain unavailable"

credential_status=0
blob=$(security find-generic-password -s "Claude Code-credentials" -w 2>/dev/null) || credential_status=$?
if [ -z "$blob" ]; then
  if [ "$credential_status" -ne 44 ] && [ "$credential_status" -ne 0 ]; then
    # Log every ACL failure with its exit code so a future storm has a paper
    # trail. Exit 44 is item-not-found and is not a permission issue.
    mkdir -p "${HOME}/.claude" 2>/dev/null
    log="${HOME}/.claude/acl-events.log"
    printf '%s security exit=%d\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$credential_status" >> "$log"

    # Post one Notification Center banner per hour across every session on
    # this machine. Atomic via mkdir: concurrent paints all try to claim the
    # same lock directory, only one succeeds. A stale lock older than an hour
    # is cleared so the next real failure can post.
    notify_lock=/tmp/claude-usage.acl-notify.lock
    _fire_notify=0
    if mkdir "$notify_lock" 2>/dev/null; then
      _fire_notify=1
    else
      stamp=$(stat -f %m "$notify_lock" 2>/dev/null || echo 0)
      if [ $(( now - stamp )) -gt 3600 ]; then
        rmdir "$notify_lock" 2>/dev/null || true
        mkdir "$notify_lock" 2>/dev/null && _fire_notify=1
      fi
    fi
    if [ "$_fire_notify" -eq 1 ]; then
      osascript >/dev/null 2>&1 <<APPLESCRIPT &
display notification "Restart Claude Code to restore. Log: ~/.claude/acl-events.log" with title "Fable usage: Keychain access denied" subtitle "security exit=$credential_status" sound name "Basso"
APPLESCRIPT
    fi
    emit_stale "keychain unavailable"
  fi
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
      fetched_at: now | floor,
      ok: true
    }
' "$tmp" | tee "$cache"
rm -f "$tmp"
