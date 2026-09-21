- This repo is the global config for this machine, not just a project. `.agents/AGENTS.md` is symlinked into Claude (as `~/.claude/CLAUDE.md`), Codex, Gemini, and Grok. Never add a `CLAUDE.md` or `.claude/CLAUDE.md` to this repo: Claude Code then reads it instead of this file. Put rules that must reach every agent in `.agents/AGENTS.md`; keep this file for facts about the machine and this repo's workflow.
- Most config files in `~/.config/` are symlinked from this repo, but some (like `helix/languages.toml`) are separate copies with a split/merge workflow (e.g. to keep secrets out of the repo). Before editing a config, check with `readlink` whether the live file is a symlink or a copy. If it's a copy, edit the live file at `~/.config/` directly, then sync the change back here.
- For Claude Code flags, model settings, or hook behavior, check current official
  docs and the installed bundle under `~/.local/share/claude/versions/` before
  editing. Version-specific memory and old presets are not current evidence.
- The Mouse Keys Option-five-times toggle and built-in-trackpad behavior are
  deliberate because they let Tyler disable the trackpad for his daughter.
  Dock autohide stays on. `com.zeitalabs.jottleai` is Monologue and remains
  excluded from synced defaults because it stores account data.
- The `claude()` GPG prewarm already heals a stale keyboxd lock. Diagnose its
  current log and code before changing the wrapper; do not restart
  `gpg-agent` as a first response.
- The bidirectional syncs (macOS defaults, browser Local State, VS Code,
  Graphite, Helix) merge three ways against the last synced state in
  `~/.local/state/dotfiles-sync/`; the live machine wins a conflict. Every
  write to the live machine is logged in `~/Library/Logs/dotfiles-sync.log`.
  `sync-macos-defaults.py --dry-run` previews; `--adopt` is for a fresh Mac.
