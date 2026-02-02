#!/bin/bash
# Lookup PR state/CI/mergeable using ETags to avoid rate limits
# Usage: gh-pr-status <repo> <pr_number>
# Outputs: state:ci:mergeable (e.g., "approved:pass:ok", "pending:fail:conflict")

repo="$1"
pr_num="$2"
[[ -z "$repo" || -z "$pr_num" ]] && exit 0

cache_dir="$HOME/.cache/gh-pr-etag"
mkdir -p "$cache_dir"
cache_key="${repo//\//_}_$pr_num"

# Fetch with ETag support. Returns cached data if 304, fresh data if 200.
# Usage: fetch_with_etag <endpoint> <cache_suffix>
fetch_with_etag() {
  local endpoint="$1"
  local suffix="$2"
  local cache_file="$cache_dir/${cache_key}_$suffix"
  local etag_file="$cache_file.etag"

  local etag_header=()
  [[ -f "$etag_file" ]] && etag_header=(-H "If-None-Match: $(cat "$etag_file")")

  local response
  response=$(gh api -i "$endpoint" "${etag_header[@]}" 2>/dev/null)

  if echo "$response" | head -1 | grep -q "304"; then
    cat "$cache_file" 2>/dev/null
    return
  fi

  if echo "$response" | head -1 | grep -q "200"; then
    # Save ETag
    echo "$response" | grep -i '^Etag:' | awk '{print $2}' | tr -d '\r\n' > "$etag_file"
    # Extract JSON body (after blank line)
    local json
    json=$(echo "$response" | sed -n '/^\r*$/,$p' | tail -n +2)
    echo "$json" > "$cache_file"
    echo "$json"
  fi
}

# Fetch PR data
pr_json=$(fetch_with_etag "repos/$repo/pulls/$pr_num" "pr")
[[ -z "$pr_json" ]] && exit 0

# Get review status
get_review_state() {
  local reviews_json
  reviews_json=$(fetch_with_etag "repos/$repo/pulls/$pr_num/reviews" "reviews")

  # Get latest review per user, check for approvals/changes requested
  local dominated_reviews
  dominated_reviews=$(echo "$reviews_json" | jq -r '[.[] | select(.state != "COMMENTED")] | group_by(.user.login) | map(last) | .[].state' 2>/dev/null)

  if echo "$dominated_reviews" | grep -q "CHANGES_REQUESTED"; then
    echo "changes_requested"
  elif echo "$dominated_reviews" | grep -q "APPROVED"; then
    echo "approved"
  else
    echo "pending"
  fi
}

# Get CI status
get_ci_state() {
  local head_sha
  head_sha=$(echo "$pr_json" | jq -r '.head.sha')
  [[ -z "$head_sha" || "$head_sha" == "null" ]] && echo "pass" && return

  local checks_json
  checks_json=$(fetch_with_etag "repos/$repo/commits/$head_sha/check-runs" "checks_$head_sha")

  if echo "$checks_json" | jq -e '.check_runs[] | select(.conclusion == "failure")' &>/dev/null; then
    echo "fail"
  else
    echo "pass"
  fi
}

# Get mergeable status
get_mergeable_state() {
  local mergeable
  mergeable=$(echo "$pr_json" | jq -r '.mergeable')
  if [[ "$mergeable" == "false" ]]; then
    echo "conflict"
  else
    echo "ok"
  fi
}

# Check if PR is draft or merged
state=$(echo "$pr_json" | jq -r '.state')
is_draft=$(echo "$pr_json" | jq -r '.draft')

if [[ "$state" == "closed" ]]; then
  # Check if merged
  if [[ $(echo "$pr_json" | jq -r '.merged') == "true" ]]; then
    echo "merged:pass:ok"
  else
    echo "closed:pass:ok"
  fi
elif [[ "$is_draft" == "true" ]]; then
  echo "draft:$(get_ci_state):$(get_mergeable_state)"
else
  echo "$(get_review_state):$(get_ci_state):$(get_mergeable_state)"
fi
