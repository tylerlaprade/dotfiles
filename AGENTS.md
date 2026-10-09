- This repo is the global config for this machine, not just a project. `.agents/AGENTS.md` is symlinked into Claude (as `~/.claude/CLAUDE.md`), Codex, Gemini, and Grok. Never add a `CLAUDE.md` or `.claude/CLAUDE.md` to this repo: Claude Code then reads it instead of this file. Put rules that must reach every agent in `.agents/AGENTS.md`; keep this file for facts about the machine and this repo's workflow.
- Most config files in `~/.config/` are symlinked from this repo, but some (like `helix/languages.toml`) are separate copies with a split/merge workflow (e.g. to keep secrets out of the repo). Before editing a config, check with `readlink` whether the live file is a symlink or a copy. If it's a copy, edit the live file at `~/.config/` directly, then sync the change back here.
- For Claude Code flags, model settings, or hook behavior, check current official
  docs and the installed bundle under `~/.local/share/claude/versions/` before
  editing. Version-specific memory and old presets are not current evidence.
- `docs/claude-code-context-and-cache.md` explains the compaction, idle, and
  cache settings in `.claude/settings.json`, the options left off, and a
  keepalive design that was measured but not built.
- Kid mode blocks pointer input with an event tap in `scripts/kid_trackpad.py`,
  run by a launch agent that follows the root Kanata daemon's TCP port. A launch
  daemon cannot create that tap on macOS 15, and seizing the trackpad's HID
  device has no effect on this Mac. Do not bring back Mouse Keys or “Ignore
  built-in trackpad”: any mouse, including a virtual one, then disables the
  trackpad even while unlocked, so shared defaults keep that setting off.
- `kanata-setup` (`scripts/bin/kanata-setup.sh`, also run by `install.sh`)
  installs Homebrew's Kanata with the Karabiner driver package that Kanata
  release supports, read from the socket the binary expects (v6.2.0 before
  Kanata 1.13, v8.0.0 after), and removes Karabiner-Elements, whose newer
  driver and keyboard grabs broke Kanata. `~/.local/bin/kanata` is signed with
  the Developer ID as `com.tylerlaprade.kanata`, so its Input Monitoring and
  Accessibility grants survive upgrades.
- Dock autohide stays on. `com.zeitalabs.jottleai` is Monologue and remains
  excluded from synced defaults because it stores account data.
- GPG uses Homebrew's `pinentry-mac` and macOS Keychain for its passphrase.
  Claude startup does not prewarm GPG. Reload changed agent settings with
  `gpgconf --reload gpg-agent`; do not kill the agent during signed work.
- The daily sync only links config and merges defaults. It does not install
  tools. `install.sh` is the one installer. On an existing Mac it installs
  only what is missing: `brew bundle --no-upgrade`, the three-way defaults
  sync (live settings win), and `kanata-setup`.
  Before asking Tyler to run it, read what it will do and prefer the one
  step that needs his password. Monologue is the Homebrew cask.
- The bidirectional syncs (macOS defaults, browser Local State, Graphite,
  Helix) merge three ways against the last synced state in
  `~/.local/state/dotfiles-sync/`; the live machine wins a conflict. With no
  recorded base, a repo that already has content is adopted and the live
  machine is not exported over it. VS Code is not installed or synced. Every
  write to the live machine is logged in `~/Library/Logs/dotfiles-sync.log`.
  `sync-macos-defaults.py --dry-run` previews; `--adopt` forces every repo
  value over the live machine, and nothing runs it automatically.
- Python here runs on Homebrew's `/opt/homebrew/bin/python3`, named in each
  shebang and shell caller, and the checkers target its version. The
  exceptions are `sync-macos-defaults.py`, `sync-browser-local-state.py`,
  `sync-graphite.py`, `apply-macos-defaults.py`, the `threeway.py` they
  import, and `kid_trackpad.py`: they stay on Apple's `/usr/bin/python3` (3.9)
  so macOS Automation, App Data, and Accessibility grants survive Homebrew
  upgrades, and `ruff.toml` and `pyrightconfig.json` check those files as 3.9.
- Run the Python tests in `tests/` from the repo root as
  `/opt/homebrew/bin/python3 -m unittest tests/<name>.py`; `tests/threeway.py`
  imports the sync module as `scripts.sync.threeway`, which resolves only from
  there. The recall skill's tests run from its root with `PYTHONPATH=scripts`.
