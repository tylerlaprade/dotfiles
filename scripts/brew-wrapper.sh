#!/bin/bash
# Experimental brew wrapper — runs wax and zerobrew in parallel instead of Homebrew.
# Source this file to override the `brew` command.
# Compatible with both bash 3.2+ and zsh.
#
# Both tools are tested side-by-side:
#   wax   → installs to /opt/homebrew (same cellar, packages are real)
#   zb    → installs to /opt/zerobrew (isolated experiment)

# Temp dir for collecting results (unique per shell process)
_BREW_EXP_DIR="${TMPDIR:-/tmp}/brew-experiment-$$"

# Sanitize package name for use as filename (slashes in tap formulas)
_brew_exp_safename() { echo "${1//\//__}"; }

brew() {
  case "$1" in
    install)
      shift
      _brew_exp_install "$@"
      ;;
    bundle)
      shift
      _brew_exp_bundle "$@"
      ;;
    *)
      command brew "$@"
      ;;
  esac
}

_brew_exp_install() {
  local pkgs=() flags=()
  for arg in "$@"; do
    if [[ "$arg" == -* ]]; then
      flags+=("$arg")
    else
      pkgs+=("$arg")
    fi
  done

  [[ ${#pkgs[@]} -eq 0 ]] && return 0

  local has_wax=false has_zb=false
  command -v wax &>/dev/null && has_wax=true
  command -v zb &>/dev/null && has_zb=true

  if ! $has_wax && ! $has_zb; then
    echo "brew-wrapper: neither wax nor zb found, falling back to brew"
    command brew install "$@"
    return
  fi

  rm -rf "$_BREW_EXP_DIR"
  mkdir -p "$_BREW_EXP_DIR/wax" "$_BREW_EXP_DIR/zb"

  # Run experimental tools in parallel
  local pids=()
  for pkg in "${pkgs[@]}"; do
    local safe=$(_brew_exp_safename "$pkg")
    if $has_wax; then
      ( wax install "$pkg" >"$_BREW_EXP_DIR/wax/$safe.out" 2>&1; echo $? >"$_BREW_EXP_DIR/wax/$safe.rc" ) &
      pids+=($!)
    fi
    if $has_zb; then
      ( zb install "$pkg" >"$_BREW_EXP_DIR/zb/$safe.out" 2>&1; echo $? >"$_BREW_EXP_DIR/zb/$safe.rc" ) &
      pids+=($!)
    fi
  done

  for pid in "${pids[@]}"; do
    wait "$pid" 2>/dev/null
  done

  _brew_exp_report "${pkgs[@]}"

  # Fall back to real brew for anything that failed in both tools
  local brew_fallback=()
  for pkg in "${pkgs[@]}"; do
    local safe=$(_brew_exp_safename "$pkg")
    local wax_ok=false zb_ok=false
    $has_wax && [[ -f "$_BREW_EXP_DIR/wax/$safe.rc" ]] && [[ "$(cat "$_BREW_EXP_DIR/wax/$safe.rc")" == "0" ]] && wax_ok=true
    $has_zb && [[ -f "$_BREW_EXP_DIR/zb/$safe.rc" ]] && [[ "$(cat "$_BREW_EXP_DIR/zb/$safe.rc")" == "0" ]] && zb_ok=true
    if ! $wax_ok && ! $zb_ok; then
      brew_fallback+=("$pkg")
    fi
  done
  if [[ ${#brew_fallback[@]} -gt 0 ]]; then
    echo ""
    echo "brew-wrapper: falling back to brew for ${#brew_fallback[@]} failed package(s)"
    command brew install "${flags[@]}" "${brew_fallback[@]}"
  fi
}

_brew_exp_bundle() {
  local brewfile=""
  local extra_flags=()

  while [[ $# -gt 0 ]]; do
    case "$1" in
      --file=*) brewfile="${1#--file=}"; shift ;;
      --file)   brewfile="$2"; shift 2 ;;
      *)        extra_flags+=("$1"); shift ;;
    esac
  done

  if [[ -z "$brewfile" ]]; then
    if [[ -f "./Brewfile" ]]; then
      brewfile="./Brewfile"
    else
      echo "brew-wrapper: no Brewfile found"
      return 1
    fi
  fi

  if [[ ! -f "$brewfile" ]]; then
    echo "brew-wrapper: Brewfile not found: $brewfile"
    return 1
  fi

  local has_wax=false has_zb=false
  command -v wax &>/dev/null && has_wax=true
  command -v zb &>/dev/null && has_zb=true

  if ! $has_wax && ! $has_zb; then
    echo "brew-wrapper: neither wax nor zb found, falling back to brew"
    command brew bundle --file="$brewfile" "${extra_flags[@]}"
    return
  fi

  rm -rf "$_BREW_EXP_DIR"
  mkdir -p "$_BREW_EXP_DIR/wax" "$_BREW_EXP_DIR/zb"

  # Parse Brewfile into taps, formulas, casks (using sed — works in bash 3.2 and zsh)
  local taps=() formulas=() casks=()
  local line pkg
  while IFS= read -r line; do
    line="${line%%#*}"
    [[ -z "${line// /}" ]] && continue

    pkg=$(echo "$line" | sed -n 's/^tap  *"\([^"]*\)".*/\1/p')
    if [[ -n "$pkg" ]]; then
      taps+=("$pkg")
      continue
    fi

    pkg=$(echo "$line" | sed -n 's/^brew  *"\([^"]*\)".*/\1/p')
    if [[ -n "$pkg" ]]; then
      formulas+=("$pkg")
      # Note Ruby options we can't pass to wax/zb
      if echo "$line" | grep -qE 'restart_service|link:|conflicts_with'; then
        local opts="${line#*,}"
        echo "$pkg: ${opts## }" >>"$_BREW_EXP_DIR/skipped_opts.txt"
      fi
      continue
    fi

    pkg=$(echo "$line" | sed -n 's/^cask  *"\([^"]*\)".*/\1/p')
    if [[ -n "$pkg" ]]; then
      casks+=("$pkg")
      continue
    fi
  done < "$brewfile"

  local all_pkgs=("${formulas[@]}" "${casks[@]}")
  local pids=()

  echo "=== brew-wrapper: installing ${#taps[@]} taps, ${#formulas[@]} formulas, ${#casks[@]} casks ==="
  echo ""

  # --- zerobrew: run zb bundle as a single command ---
  if $has_zb; then
    (
      zb bundle -f "$brewfile" >"$_BREW_EXP_DIR/zb/_bundle.out" 2>&1
      echo $? >"$_BREW_EXP_DIR/zb/_bundle.rc"
      # Parse output to determine per-package results
      for p in "${all_pkgs[@]}"; do
        local safe=$(_brew_exp_safename "$p")
        if grep -qi "error.*${p}\|${p}.*fail\|${p}.*not found" "$_BREW_EXP_DIR/zb/_bundle.out" 2>/dev/null; then
          grep -i "$p" "$_BREW_EXP_DIR/zb/_bundle.out" >"$_BREW_EXP_DIR/zb/$safe.out" 2>/dev/null
          echo 1 >"$_BREW_EXP_DIR/zb/$safe.rc"
        else
          echo 0 >"$_BREW_EXP_DIR/zb/$safe.rc"
          : >"$_BREW_EXP_DIR/zb/$safe.out"
        fi
      done
    ) &
    pids+=($!)
  fi

  # --- wax: add taps first, then install packages in parallel ---
  if $has_wax; then
    (
      for tap in "${taps[@]}"; do
        wax tap add "$tap" >"$_BREW_EXP_DIR/wax/_tap_${tap//\//_}.out" 2>&1 || true
      done

      local install_pids=()
      for p in "${all_pkgs[@]}"; do
        local safe=$(_brew_exp_safename "$p")
        (
          wax install "$p" >"$_BREW_EXP_DIR/wax/$safe.out" 2>&1
          echo $? >"$_BREW_EXP_DIR/wax/$safe.rc"
        ) &
        install_pids+=($!)
      done
      for pid in "${install_pids[@]}"; do
        wait "$pid" 2>/dev/null
      done
    ) &
    pids+=($!)
  fi

  for pid in "${pids[@]}"; do
    wait "$pid" 2>/dev/null
  done

  _brew_exp_report "${all_pkgs[@]}"

  # Fall back to real brew bundle for anything that failed in both tools
  local brew_fallback=()
  for pkg in "${all_pkgs[@]}"; do
    local safe=$(_brew_exp_safename "$pkg")
    local wax_ok=false zb_ok=false
    $has_wax && [[ -f "$_BREW_EXP_DIR/wax/$safe.rc" ]] && [[ "$(cat "$_BREW_EXP_DIR/wax/$safe.rc")" == "0" ]] && wax_ok=true
    $has_zb && [[ -f "$_BREW_EXP_DIR/zb/$safe.rc" ]] && [[ "$(cat "$_BREW_EXP_DIR/zb/$safe.rc")" == "0" ]] && zb_ok=true
    if ! $wax_ok && ! $zb_ok; then
      brew_fallback+=("$pkg")
    fi
  done
  if [[ ${#brew_fallback[@]} -gt 0 ]]; then
    echo ""
    echo "brew-wrapper: falling back to brew for ${#brew_fallback[@]} failed package(s)"
    for pkg in "${brew_fallback[@]}"; do
      command brew install "$pkg" || true
    done
    # Also handle taps that wax may have missed
    for tap in "${taps[@]}"; do
      command brew tap "$tap" 2>/dev/null || true
    done
  fi
}

_brew_exp_report() {
  local pkgs=("$@")
  local has_wax=false has_zb=false
  command -v wax &>/dev/null && has_wax=true
  command -v zb &>/dev/null && has_zb=true

  local wax_ok=0 wax_fail=0 zb_ok=0 zb_fail=0
  local wax_fail_details="" zb_fail_details=""

  for pkg in "${pkgs[@]}"; do
    local safe=$(_brew_exp_safename "$pkg")
    if $has_wax; then
      local rc_file="$_BREW_EXP_DIR/wax/$safe.rc"
      if [[ -f "$rc_file" ]] && [[ "$(cat "$rc_file")" == "0" ]]; then
        wax_ok=$((wax_ok + 1))
      else
        wax_fail=$((wax_fail + 1))
        local err=""
        [[ -f "$_BREW_EXP_DIR/wax/$safe.out" ]] && err="$(tail -3 "$_BREW_EXP_DIR/wax/$safe.out")"
        wax_fail_details="${wax_fail_details}${pkg}|${err}
"
      fi
    fi
    if $has_zb; then
      local rc_file="$_BREW_EXP_DIR/zb/$safe.rc"
      if [[ -f "$rc_file" ]] && [[ "$(cat "$rc_file")" == "0" ]]; then
        zb_ok=$((zb_ok + 1))
      else
        zb_fail=$((zb_fail + 1))
        local err=""
        [[ -f "$_BREW_EXP_DIR/zb/$safe.out" ]] && err="$(tail -3 "$_BREW_EXP_DIR/zb/$safe.out")"
        zb_fail_details="${zb_fail_details}${pkg}|${err}
"
      fi
    fi
  done

  echo ""
  echo "=== Experimental Package Manager Results ==="
  echo ""
  local total=${#pkgs[@]}
  $has_wax && echo "wax:       $wax_ok/$total succeeded, $wax_fail failed"
  $has_zb  && echo "zerobrew:  $zb_ok/$total succeeded, $zb_fail failed"

  if [[ -n "$wax_fail_details" ]]; then
    echo ""
    echo "wax failures:"
    echo "$wax_fail_details" | while IFS='|' read -r pkg err; do
      [[ -z "$pkg" ]] && continue
      echo "  - $pkg"
      [[ -n "$err" ]] && echo "$err" | sed 's/^/      /'
    done
  fi

  if [[ -n "$zb_fail_details" ]]; then
    echo ""
    echo "zerobrew failures:"
    echo "$zb_fail_details" | while IFS='|' read -r pkg err; do
      [[ -z "$pkg" ]] && continue
      echo "  - $pkg"
      [[ -n "$err" ]] && echo "$err" | sed 's/^/      /'
    done
  fi

  if [[ -f "$_BREW_EXP_DIR/skipped_opts.txt" ]]; then
    echo ""
    echo "Skipped (unsupported Brewfile options):"
    sed 's/^/  - /' "$_BREW_EXP_DIR/skipped_opts.txt"
  fi

  # Prompt to file issues for each tool
  if [[ -n "$wax_fail_details" ]]; then
    _brew_exp_prompt_issue "wax" "plyght/wax" "$wax_fail" "$wax_fail_details"
  fi
  if [[ -n "$zb_fail_details" ]]; then
    _brew_exp_prompt_issue "zerobrew" "lucasgelfond/zerobrew" "$zb_fail" "$zb_fail_details"
  fi

  rm -rf "$_BREW_EXP_DIR"
}

_brew_exp_prompt_issue() {
  local tool_name="$1" repo="$2" fail_count="$3" fail_details="$4"

  echo ""
  printf "File a %s issue with %d failure(s)? [y/N] " "$tool_name" "$fail_count"
  read -r reply || reply=""
  if [[ "$reply" =~ ^[Yy]$ ]]; then
    local title="Package install failures ($(date +%Y-%m-%d))"
    local body="The following packages failed to install via \`$tool_name\`:"
    body="$body

"
    local count=0
    # Use here-string (not pipe) so variable modifications persist
    while IFS='|' read -r pkg err; do
      [[ -z "$pkg" ]] && continue
      count=$((count + 1))
      body="$body### \`$pkg\`
\`\`\`
${err}
\`\`\`

"
      if [[ $count -ge 10 ]]; then
        body="$body_(truncated - $fail_count total failures)_
"
        break
      fi
    done <<< "$fail_details"
    body="$body**Environment:** $(uname -mrs), $($tool_name --version 2>/dev/null || echo 'version unknown')"

    local encoded_title encoded_body
    encoded_title=$(_brew_exp_urlencode "$title")
    encoded_body=$(_brew_exp_urlencode "$body")
    open "https://github.com/$repo/issues/new?title=$encoded_title&body=$encoded_body"
  fi
}

_brew_exp_urlencode() {
  python3 -c "import urllib.parse, sys; print(urllib.parse.quote(sys.argv[1], safe=''))" "$1"
}
