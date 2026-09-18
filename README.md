# dotfiles

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/tylerlaprade/dotfiles/master/bootstrap.sh)"
```

## Agent instructions

Two files, different reach.

`.agents/AGENTS.md` holds the rules that must reach every agent. It is
symlinked to `~/.agents/AGENTS.md`, `~/.codex/AGENTS.md`,
`~/.grok/rules/agents.md`, and `~/.gemini/GEMINI.md` (Antigravity took that
directory over), so an edit there changes Claude, Codex, Grok, and Antigravity
at once. `.claude/CLAUDE.md` adds the Claude-only layer and is symlinked to
`~/.claude/CLAUDE.md`.

`AGENTS.md` at the root holds notes for working in this repo: which live files
are symlinks and which are copies, deliberate macOS settings that look like
bugs, and the like. Every harness reads it when the working directory is this
repo. Claude Code reaches it through the `@AGENTS.md` import in `CLAUDE.md`;
Codex, Grok, Antigravity, and opencode read it on their own.

Comments do not hide anything. Claude Code strips `<!-- ... -->` out of these
files, but Codex, Grok, Antigravity, and opencode all pass it straight to the
model.

## Second Mac

Both machines stay in use, so nothing is wiped and nothing is zipped. The
repo carries the configuration; each machine logs in on its own.

1. Clone this repo to `~/Code/dotfiles` and run `install.sh`. It installs the
   tools, links the configs, applies `/etc/hosts` from `scripts/setup/hosts`,
   loads the Kanata daemon, and adopts the captured macOS defaults as the sync
   base. From then on the daily sync merges changes both ways.
2. Log in: `gh auth login`, `claude`, `codex`, `gemini`, `grok`, `gt auth`,
   `sourcery login`, `aws configure`.
3. Keys: create a new SSH key on the machine and add it to GitHub. Export the
   GPG signing key from the first Mac (`gpg --export-secret-keys`) and import
   it, so commits sign as the same key.
4. Code signing: on the first Mac, Xcode > Settings > Accounts > Export Apple
   ID and Code Signing Assets; open the file on the second Mac.
5. Grant Input Monitoring and Accessibility to `~/.local/bin/kanata`, and
   Accessibility to Ghostty, AltTab, and Homerow when they ask.
