# Agent instructions

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
