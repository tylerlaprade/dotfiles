#!/bin/bash
# netcup — call the netcup Server Control Panel REST API.
#
#   netcup login                     approve a device sign-in once; keeps a
#                                    refresh token in Keychain item
#                                    "netcup-scp-refresh-token"
#   netcup METHOD PATH [curl args]   e.g. netcup GET /servers
#
# PATH is relative to https://www.servercontrolpanel.de/scp-core/api/v1; the
# OpenAPI spec is at that URL plus /openapi. Netcup rotates the refresh token
# on use, so each call saves the new one.
set -euo pipefail

keychain_item=netcup-scp-refresh-token
oidc=https://www.servercontrolpanel.de/realms/scp/protocol/openid-connect
api=https://www.servercontrolpanel.de/scp-core/api/v1

save_refresh_token() {
  security add-generic-password -U -a "$USER" -s "$keychain_item" -w "$1"
}

login() {
  local device device_code interval response error refresh_token
  device=$(curl -fsS -X POST "$oidc/auth/device" -d client_id=scp -d 'scope=offline_access openid')
  device_code=$(jq -r .device_code <<<"$device")
  interval=$(jq -r .interval <<<"$device")
  jq -r '"Approve within \(.expires_in / 60 | floor) minutes: \(.verification_uri_complete)"' <<<"$device" >&2
  while true; do
    sleep "$interval"
    response=$(curl -sS -X POST "$oidc/token" -d client_id=scp \
      -d grant_type=urn:ietf:params:oauth:grant-type:device_code -d device_code="$device_code")
    error=$(jq -r '.error // empty' <<<"$response")
    case $error in
      '')
        refresh_token=$(jq -r .refresh_token <<<"$response")
        save_refresh_token "$refresh_token"
        echo "netcup: signed in" >&2
        return
        ;;
      authorization_pending | slow_down) ;;
      *)
        echo "netcup: sign-in failed: $error" >&2
        return 1
        ;;
    esac
  done
}

access_token() {
  local refresh_token response rotated
  refresh_token=$(security find-generic-password -a "$USER" -s "$keychain_item" -w) || {
    echo "netcup: no refresh token; run: netcup login" >&2
    return 1
  }
  response=$(curl -fsS -X POST "$oidc/token" -d client_id=scp -d grant_type=refresh_token -d refresh_token="$refresh_token")
  rotated=$(jq -r '.refresh_token // empty' <<<"$response")
  if [[ -n $rotated && $rotated != "$refresh_token" ]]; then
    save_refresh_token "$rotated"
  fi
  jq -r .access_token <<<"$response"
}

case ${1:-} in
  login) login ;;
  GET | POST | PUT | PATCH | DELETE)
    [[ $# -ge 2 ]] || { echo "usage: netcup METHOD PATH [curl args]" >&2; exit 2; }
    method=$1 endpoint=$2
    shift 2
    content_type=application/json
    [[ $method == PATCH ]] && content_type=application/merge-patch+json
    token=$(access_token)
    curl -fsS -X "$method" -H "Authorization: Bearer $token" -H "Content-Type: $content_type" "$api$endpoint" "$@"
    ;;
  *)
    echo "usage: netcup login | netcup METHOD PATH [curl args]" >&2
    exit 2
    ;;
esac
