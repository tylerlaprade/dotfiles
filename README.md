# dotfiles

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/tylerlaprade/dotfiles/master/bootstrap.sh)"
```

## Agent instructions

Two files, different reach.

`.agents/AGENTS.md` holds the rules that must reach every agent. It is
symlinked to `~/.agents/AGENTS.md`, `~/.codex/AGENTS.md`, and
`~/.grok/rules/agents.md`, so an edit there changes Claude, Codex, and Grok at
once. `.claude/CLAUDE.md` adds the Claude-only layer and is symlinked to
`~/.claude/CLAUDE.md`.

`AGENTS.md` at the root holds notes for working in this repo: which live files
are symlinks and which are copies, deliberate macOS settings that look like
bugs, and the like. Every harness reads it when the working directory is this
repo. Claude Code reaches it through the `@AGENTS.md` import in `CLAUDE.md`;
Codex, Grok, and opencode read it on their own.

Comments do not hide anything. Claude Code strips `<!-- ... -->` out of these
files, but Codex, Grok, and opencode all pass it straight to the model.

