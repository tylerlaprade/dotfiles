- This repo is the global config for this machine, not just a project. `.agents/AGENTS.md` is symlinked into Claude (as `~/.claude/CLAUDE.md`), Codex, Gemini, and Grok. Never add a `CLAUDE.md` or `.claude/CLAUDE.md` to this repo: Claude Code then reads it instead of this file. Put rules that must reach every agent in `.agents/AGENTS.md`; keep this file for facts about the machine and this repo's workflow.
- Most config files in `~/.config/` are symlinked from this repo, but some (like `helix/languages.toml`) are separate copies with a split/merge workflow (e.g. to keep secrets out of the repo). Before editing a config, check with `readlink` whether the live file is a symlink or a copy. If it's a copy, edit the live file at `~/.config/` directly, then sync the change back here.
- For Claude Code flags, model settings, or hook behavior, check current official
  docs and the installed bundle under `~/.local/share/claude/versions/` before
  editing. Version-specific memory and old presets are not current evidence.
- The Mouse Keys Option-five-times toggle and built-in-trackpad behavior are
  deliberate in the standalone Kanata config. The installed Kanata launch
  daemon uses the virtual mouse controller instead; it builds its helper at
  startup and uses the existing lock/unlock shortcuts. Install the root daemon
  only after linking config and applying the trackpad preferences.
  Dock autohide stays on. `com.zeitalabs.jottleai` is Monologue and remains
  excluded from synced defaults because it stores account data.
- The `claude()` GPG prewarm already heals a stale keyboxd lock. Diagnose its
  current log and code before changing the wrapper; do not restart
  `gpg-agent` as a first response.
- The daily sync only links config and merges defaults. It does not install
  tools. `install.sh` is the one installer, and it is safe to run again:
  steps that are already done stop. Monologue is the Homebrew cask.
- The bidirectional syncs (macOS defaults, browser Local State, Graphite,
  Helix) merge three ways against the last synced state in
  `~/.local/state/dotfiles-sync/`; the live machine wins a conflict. With no
  recorded base, a repo that already has content is adopted and the live
  machine is not exported over it. VS Code is not installed or synced. Every
  write to the live machine is logged in `~/Library/Logs/dotfiles-sync.log`.
  `sync-macos-defaults.py --dry-run` previews; `--adopt` is for a fresh Mac.
- Python here runs on Homebrew's `/opt/homebrew/bin/python3`, named in each
  shebang and shell caller, and the checkers target its version. The
  exceptions are `sync-macos-defaults.py`, `sync-browser-local-state.py`,
  `sync-graphite.py`, `apply-macos-defaults.py`, and the `threeway.py` they
  import: they stay on Apple's `/usr/bin/python3` (3.9) so macOS Automation
  and App Data grants survive Homebrew upgrades, and `ruff.toml` and
  `pyrightconfig.json` check those files as 3.9.
- Run the Python tests in `tests/` from the repo root as
  `/opt/homebrew/bin/python3 -m unittest tests/<name>.py`; `tests/threeway.py`
  imports the sync module as `scripts.sync.threeway`, which resolves only from
  there. The recall skill's tests run from its root with `PYTHONPATH=scripts`.
