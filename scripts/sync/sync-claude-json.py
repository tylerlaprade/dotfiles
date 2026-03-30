#!/usr/bin/env -S uv run --script
"""Bidirectional sync of ~/.claude.json preferences.

Keeps user preferences in sync between the repo fragment and the local
``~/.claude.json`` using last-writer-wins semantics, while leaving
machine-local state (counters, caches, tokens, feature flags) untouched.

Uses a blacklist so new preferences added by Claude Code sync by default.

Usage: sync-claude-json.py <repo_prefs> <local_file>
"""

import json
import os
import re
import sys

prefs_path, local_path = sys.argv[1], sys.argv[2]

# Blacklist — machine-local keys that must NOT travel between machines.
# Everything else is treated as a syncable preference.
LOCAL_ONLY_KEYS = {
    "anonymousId", "userID", "oauthAccount",
    "installMethod", "firstStartTime", "claudeCodeFirstTokenDate",
    "numStartups",
    "projects", "mcpServers", "githubRepoPaths",
    "tipsHistory", "toolUsage", "skillUsage",
    "clientDataCache", "feedbackSurveyState",
    "shiftEnterKeyBindingInstalled",
    "officialMarketplaceAutoInstallAttempted", "officialMarketplaceAutoInstalled",
    "claudeAiMcpEverConnected",
    "isQualifiedForDataSharing", "penguinModeOrgEnabled",
    "hasAvailableSubscription", "recommendedSubscription",
    "lastOnboardingVersion", "lastPlanModeUse", "lastReleaseNotesSeen",
    "changelogLastFetched",
    "autoUpdatesProtectedForNative",
    "fallbackAvailableWarningThreshold",
    "hasOpusPlanDefault",
}

LOCAL_ONLY_PATTERNS = [
    r"^cached",        # cachedStatsigGates, cachedDynamicConfigs, …
    r"Cache$",         # groveConfigCache, s1mAccessCache, …
    r"^hasSeen",       # one-time hints
    r"^hasShown",      # one-time notices
    r"^hasVisited",    # one-time visits
    r"^hasUsed",       # one-time usage flags
    r"^hasCompleted",  # one-time completion flags
    r"^hasIde",        # IDE one-time dialogs
    r"Count$",         # counters (*Count)
    r"Migration",      # migration flags
    r"Dismissed$",     # dismissed callouts
]


def is_local(key):
    if key in LOCAL_ONLY_KEYS:
        return True
    return any(re.search(p, key) for p in LOCAL_ONLY_PATTERNS)


with open(prefs_path) as f:
    prefs = json.load(f)
with open(local_path) as f:
    local = json.load(f)

local_prefs = {k: v for k, v in local.items() if not is_local(k)}

repo_mtime = os.path.getmtime(prefs_path)
local_mtime = os.path.getmtime(local_path)

merged_prefs = local_prefs if local_mtime > repo_mtime else prefs

# Update repo fragment if it changed.
if prefs != merged_prefs:
    with open(prefs_path, "w") as f:
        json.dump(merged_prefs, f, indent=2)
        f.write("\n")

# Merge winning preferences back into the full local file.
merged_local = {**local, **merged_prefs}
if local != merged_local:
    with open(local_path, "w") as f:
        json.dump(merged_local, f, indent=2)
        f.write("\n")
