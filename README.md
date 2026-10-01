# dotfiles

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/tylerlaprade/dotfiles/master/bootstrap.sh)"
```

## Agent instructions

`.agents/AGENTS.md` holds the rules that reach every agent. It is symlinked to
`~/.agents/AGENTS.md`, `~/.claude/CLAUDE.md`, `~/.codex/AGENTS.md`,
`~/.grok/rules/agents.md`, and `~/.gemini/GEMINI.md` (Antigravity took that
directory over), so an edit there changes Claude, Codex, Grok, and Antigravity
at once. Claude Code has no user-level `AGENTS.md`, which is why its copy is
still named `CLAUDE.md`. Keep that link target out of this repo's `.claude/`:
Claude Code treats a `.claude/CLAUDE.md` in the working tree as project
instructions, which would stop it from reading this repo's `AGENTS.md`.

`AGENTS.md` at the root holds notes for working in this repo: which live files
are symlinks and which are copies, deliberate macOS settings that look like
bugs, and the like. Every harness reads it when the working directory is this
repo. Every harness, Claude Code included, reads `AGENTS.md` on its own; no
`CLAUDE.md` import is needed.

Comments do not hide anything. Claude Code strips `<!-- ... -->` out of these
files, but Codex, Grok, Antigravity, and opencode all pass it straight to the
model.

## Train game keyboard lock

Hold both Shift keys, then press K to enter kid mode. It also toggles Mouse
Keys, which disables the built-in trackpad with the existing Pointer Control
settings. In kid mode, only Left/Right arrows, Space, screen brightness, and
volume (including mute) work. All other keys are disabled, including the Mouse
Keys movement/click keys, Mission Control, Launchpad, media playback, Escape,
Tab, modifiers, and Fn/Globe. Hold either Shift and press K to unlock
before typing `quit` or `exit` in the game.

Kanata's built-in Left Control + Space + Escape emergency exit still works.

### Virtual mouse prototype

The temporary prototype uses the official Karabiner DriverKit client to create
a virtual mouse during kid mode. macOS can then ignore the built-in trackpad
without Mouse Keys intercepting letters. The normal Kanata config stays in
place; exiting the prototype restores the existing Kanata launch daemon.

On the Mac, run `./install.sh --kid-trackpad-prototype` from this repo. This
builds only the helper, using the official client release matching the
installed virtual HID daemon. It requires Xcode Command Line Tools and does
not change or replace the driver. Rebuild after updating the driver.

In Accessibility → Pointer Control, turn Mouse Keys off and enable
“Ignore built-in trackpad when mouse or wireless trackpad is present.” Then
run `sudo python3 scripts/kid_trackpad.py`. Both Shifts + K enters kid mode;
either Shift + K unlocks. Ordinary letters, numbers, and punctuation pass
through; modifiers, Fn, system function keys, Escape, and Tab stay disabled.
Brightness, volume, Left/Right, and Space still work.

Test pointer movement, clicks, scrolling, and Mission Control/Spaces gestures
while locked, then verify that unlocking restores the trackpad. Ctrl+C stops
the prototype and restores the normal service. A driver or Kanata connection
failure also stops the prototype and restores the service. These physical
trackpad checks have not yet been verified on the Mac; the default remains
the existing Mouse Keys setup.
Kanata reads that chord before remapping, so a layer cannot disable it.

## Second Mac

Both machines stay in use, so nothing is wiped and nothing is zipped. The
repo carries the configuration; each machine logs in on its own.

1. Clone this repo to `~/Code/dotfiles` and run `install.sh`. It installs the
   tools, links the configs, applies `/etc/hosts` from `scripts/setup/hosts`,
   loads Kanata and the user agents, and adopts the shared macOS defaults,
   login items, browser Local State, Helix, and Graphite settings as the sync
   base. A machine that has no base does not write its own defaults back over
   the repo. VS Code is not installed. `~/.gitconfig` stays on the machine and
   includes the shared file, so the CodeRabbit machine id is not shared.
   The daily sync only links config. Run `install.sh` again when the tool
   list changes; steps that are already done stop.
2. Log in: `gh auth login`, `claude`, `codex`, `gemini`, `grok`, `gt auth`,
   `sourcery login`, `aws configure`.
3. Keys: create a new SSH key on the machine and add it to GitHub before
   cloning anything else. The shared gitconfig rewrites GitHub HTTPS to SSH.
   Export the GPG signing key from the first Mac (`gpg --export-secret-keys`)
   and import it, so commits sign as the same key.
4. Code signing: on the first Mac, Xcode > Settings > Accounts > Export Apple
   ID and Code Signing Assets; open the file on the second Mac.
5. Grant Input Monitoring and Accessibility to `~/.local/bin/kanata`, and
   Accessibility to Ghostty, AltTab, and Homerow when they ask.
