- Most config files in `~/.config/` are symlinked from this repo, but some (like `helix/languages.toml`) are separate copies with a split/merge workflow (e.g. to keep secrets out of the repo). Before editing a config, check with `readlink` whether the live file is a symlink or a copy. If it's a copy, edit the live file at `~/.config/` directly, then sync the change back here.
- For Claude Code flags, model settings, or hook behavior, check current official
  docs and the installed bundle under `~/.local/share/claude/versions/` before
  editing. Version-specific memory and old presets are not current evidence.
- The Mouse Keys Option-five-times toggle and built-in-trackpad behavior are
  deliberate because they let Tyler disable the trackpad for his daughter.
  Dock autohide stays on. `com.zeitalabs.jottleai` is Monologue and remains
  excluded from synced defaults because it stores account data. Monologue
  opens itself at login through its own setting, so it must stay out of the
  macOS login items; adding it there opens the app twice.
- The `claude()` GPG prewarm already heals a stale keyboxd lock. Diagnose its
  current log and code before changing the wrapper; do not restart
  `gpg-agent` as a first response.
