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

Hold both Shift keys, then press K to enter kid mode; it plays three Tinks. Hold
either Shift and press K to unlock; it plays three Pops. In kid mode, ordinary letters, numbers, and punctuation type
normally; brightness, volume, Left/Right, and Space work. Modifiers, Fn, system
function keys, Escape, Tab, Return, and Backspace stay disabled.

The `com.tylerlaprade.kid-trackpad` launch agent runs `scripts/kid_trackpad.py`,
which follows Kanata's layer over the TCP port the Kanata daemon opens on
`127.0.0.1:41471`. While kid mode is on, an event tap drops pointer movement,
clicks, scrolling, and trackpad gestures, including Spaces and Mission Control
swipes. Keyboard and media keys never reach the tap. Unlocking turns the tap off,
and the tap ends with the agent, so a crash cannot leave the trackpad blocked.
Neither Mouse Keys nor “Ignore built-in trackpad when mouse or wireless trackpad
is present” is involved. Leave that setting off: any mouse, including a virtual
one, would then disable the trackpad even while unlocked.

`kanata-setup` installs Homebrew's Kanata with the Karabiner driver package that
Kanata release supports, without Karabiner-Elements. `install.sh` runs it; run
it alone after a Kanata upgrade. `kanata-setup --dry-run` shows what it would do.

The tap is an agent, not part of the root Kanata daemon, because macOS 15 does
not let a launch daemon create it. It runs on Apple's `/usr/bin/python3`, which
needs Accessibility once: allow `python3` in System Settings → Privacy &
Security → Accessibility. Until then the agent logs the missing permission to
`/tmp/kid-trackpad.log` and retries every ten seconds.

Kanata's built-in Left Control + Space + Escape emergency exit still works.
Kanata reads that chord before remapping, so a layer cannot disable it.

## Second Mac

Both machines stay in use, so nothing is wiped and nothing is zipped. The
repo carries the configuration; each machine logs in on its own.

1. Clone this repo to `~/Code/dotfiles` and run `install.sh`. It installs the
   tools, links the configs, applies `/etc/hosts` from `scripts/setup/hosts`,
   loads Kanata and the user agents, and runs the three-way sync for the
   shared macOS defaults, login items, browser Local State, Helix, and
   Graphite settings. A machine that has no base adopts the repo and does not
   write its own defaults back over it; one that has a base keeps its live
   changes. VS Code is not installed. `~/.gitconfig` stays on the machine and
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
