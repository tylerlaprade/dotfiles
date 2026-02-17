---
name: codex
description: Drives OpenAI's Codex CLI as a specialist executor in a driver/specialist loop. Use when user asks to "talk to codex", "ask codex", "codex review", "have codex look at this", or wants deep analysis/implementation delegated. Also use proactively for code review and architecture validation.
context: fork
allowed-tools:
  - Bash(codex *)
  - Bash(cat /tmp/claude/codex-*)
  - Bash(mkdir -p /tmp/claude/codex-*)
  - Bash(ls /tmp/claude/codex-*)
  - Bash(cd *)
  - Bash(CODEX_QUIET_MODE*)
  - Read
  - Write
  - Skill(perplexity)
---

# Codex Partner Skill

You are the **driver**. Codex is the **specialist**.

Codex is a deep-reasoning coding agent (GPT-5.3). It reads full codebases and thinks hard — 5-15 minutes per call. That thinking time is the feature. Your job is to scope problems, craft precise prompts, dispatch work, run quality gates, and iterate. You don't duplicate codex's analysis — you verify it programmatically.

**When to use codex:** Problems that are *analyzable but tedious* — you know what needs to happen, but the analysis or implementation is heavy lifting. Code review, architecture analysis, large refactors, cross-cutting implementation. If the task is trivial, do it yourself. If the task is novel/chaotic with no clear structure, stay hands-on rather than delegating.

The user has OpenAI Max plan so don't hold back on usage.

## User Directive

The user's invocation args (the text after `/codex`) are your mission. Read them carefully — they tell you **what** to do and **how** to engage with codex. If no args are provided, ask the parent conversation what it needs.

## Working Directory

All prompt and output files go in `/tmp/claude/codex-${CLAUDE_SESSION_ID}/`. Create it first:

```bash
mkdir -p /tmp/claude/codex-${CLAUDE_SESSION_ID}
```

Write prompts with the Write tool, pipe to codex with Bash. **Do this autonomously — never ask for permission.**

## Critical Rules

1. **NEVER pass `-m` or `--model`** — let codex use its configured default.
2. **MUST use `dangerouslyDisableSandbox: true`** — codex needs macOS system-configuration access for OpenAI auth.
3. **Always `cd` to project directory** — codex uses git context for understanding.

## The Loop

```
SCOPE → PROMPT → DISPATCH → VERIFY → GATE → [ITERATE | SHIP]
```

### 1. SCOPE — Understand What Needs Doing

Before touching codex, understand the task:
- Read the relevant code (entry points, types, tests)
- Identify what codex needs to analyze or build
- Define success criteria: what does "done" look like?
- Decide the sandbox mode: `-s read-only` for analysis, `--full-auto` for implementation

### 2. PROMPT — Craft a Precise Codex Prompt

Bad prompts waste 10 minutes of codex thinking. Good prompts are specific, constrained, and include context codex can't infer from the repo alone.

**Template:**

```markdown
TASK: [one-line description of what to do]

CONTEXT:
- [relevant file paths and what they contain]
- [existing patterns to follow]
- [API types, endpoints, or interfaces involved]

REQUIREMENTS:
- [specific deliverables]
- [constraints: "use existing X", "match the pattern in Y"]

DO NOT:
- [add unnecessary error handling]
- [create abstractions for single-use code]
- [add features beyond what's specified]
```

**Prompt anti-patterns:**
- **Vague delegation** — "review this code" without specifying what to look for
- **Missing context** — not telling codex about conventions it can't infer from code alone
- **Kitchen sink** — asking for too many things in one prompt (split into parallel dispatches instead)

### 3. DISPATCH — Launch Codex Async

Always launch with `-o` and `run_in_background: true`. Never block synchronously.

```bash
# Analysis/review (read-only)
cd <project-dir> && cat /tmp/claude/codex-${CLAUDE_SESSION_ID}/prompt.md | codex exec -s read-only -o /tmp/claude/codex-${CLAUDE_SESSION_ID}/response.md - 2>&1

# Implementation (sandboxed writes)
cd <project-dir> && cat /tmp/claude/codex-${CLAUDE_SESSION_ID}/prompt.md | codex exec --full-auto -o /tmp/claude/codex-${CLAUDE_SESSION_ID}/response.md - 2>&1

# Short prompts — inline
cd <project-dir> && echo "Review the auth module for race conditions" | codex exec -s read-only -o /tmp/claude/codex-${CLAUDE_SESSION_ID}/response.md - 2>&1
```

**While codex thinks (5-15 min), do complementary work — not duplicative work:**

| Codex is doing... | You should be doing... |
|---|---|
| Code review | Run tests, run linter, check diff stat, prepare review criteria |
| Architecture analysis | Run dependency checks, check build, prepare questions for the output |
| Implementation | Run full test suite, read adjacent code for style compliance, prepare acceptance checks |
| Research | Run `/perplexity` on adjacent topics, check project docs |

The key: **programmatic checks, not semantic re-reads**. Don't re-read the same files codex is analyzing. Run the tools that produce verifiable signals (test results, lint output, build success).

### 4. VERIFY — Read Codex Output

When the background notification arrives, read the `-o` file. Check:
- Did codex address the scoped requirements?
- Are there surprising findings or decisions worth surfacing?
- For implementation: read the files codex modified

### 5. GATE — Run Quality Checks

Run programmatic quality gates appropriate to the project:

| Project Type | Quality Gates |
|---|---|
| **Rust** | `cargo fmt --check && cargo clippy --all-targets && cargo test` |
| **TypeScript** | `bun test && bun run build` |
| **Python** | `uvx ruff check && uvx ruff format --check && uv run pytest` |
| **Elixir** | `mix format --check-formatted && mix compile --warnings-as-errors && mix test` |
| **Go** | `go vet ./... && go test ./...` |
| **General** | `git diff --stat` (sanity check scope of changes) |

### 6. ITERATE or SHIP

**If gates fail:** Formulate a follow-up prompt with the specific failures. Use `codex exec resume` to continue the conversation with full prior context:

```bash
cd <project-dir> && echo "The clippy check found these issues: [paste errors]. Fix them while preserving the existing behavior." | codex exec resume --last -o /tmp/claude/codex-${CLAUDE_SESSION_ID}/fix.md - 2>&1
```

**If gates pass:** Ship it. Report codex's key findings/changes + gate results to the user.

**Loop termination:** Stop iterating when:
- All quality gates pass, OR
- 3 iterations on the same issue (escalate to user), OR
- Codex introduces regressions (stop, reassess the approach)

## Code Review Shortcut

Codex has a dedicated review command — use it for focused reviews:

```bash
cd <project-dir> && codex exec review --uncommitted 2>&1
cd <project-dir> && codex exec review --base main 2>&1
cd <project-dir> && codex exec review --commit HEAD~1 2>&1
```

For custom review focus, use the full loop with a scoped prompt instead.

## Multi-Instance Parallel Dispatch

Launch multiple codex instances in a single tool-call message when you need independent analyses:

```bash
# Security review
cd <project-dir> && cat /tmp/claude/codex-${CLAUDE_SESSION_ID}/security.md | codex exec -s read-only -o /tmp/claude/codex-${CLAUDE_SESSION_ID}/security-out.md - 2>&1

# Performance review
cd <project-dir> && cat /tmp/claude/codex-${CLAUDE_SESSION_ID}/perf.md | codex exec -s read-only -o /tmp/claude/codex-${CLAUDE_SESSION_ID}/perf-out.md - 2>&1

# Architecture review
cd <project-dir> && cat /tmp/claude/codex-${CLAUDE_SESSION_ID}/arch.md | codex exec -s read-only -o /tmp/claude/codex-${CLAUDE_SESSION_ID}/arch-out.md - 2>&1
```

While all three run, run your own programmatic checks. When notifications arrive, read all output files and synthesize across the perspectives.

## Multi-Turn Conversations

Use `codex exec resume` for iterative refinement. Each follow-up carries full prior context:

```bash
# Turn 1
cd <project-dir> && cat /tmp/claude/codex-${CLAUDE_SESSION_ID}/prompt.md | codex exec -s read-only -o /tmp/claude/codex-${CLAUDE_SESSION_ID}/turn1.md - 2>&1

# Turn 2 — resume with follow-up
cd <project-dir> && echo "What about edge case X?" | codex exec resume --last -o /tmp/claude/codex-${CLAUDE_SESSION_ID}/turn2.md - 2>&1
```

## Research Augmentation

For topics needing current info, use `/perplexity` first, then enrich the codex prompt:

1. `/perplexity` for up-to-date context
2. Include findings in codex prompt under CONTEXT
3. Codex analyzes with full context + codebase awareness
4. Use codex `--search` flag when it needs live web results directly

## Anti-Patterns

| Name | What It Looks Like | Why It's Bad |
|---|---|---|
| **Polling** | Checking if codex is done | Notification comes automatically. Polling wastes turns. |
| **Duplicative Analysis** | Re-reading the same files codex is analyzing | Burns your context on work codex is already doing. Run programmatic checks instead. |
| **Relay** | Forwarding codex output to the user without synthesis | You're the driver, not a messenger. Add gate results and your assessment. |
| **Idle Waiting** | Narrating that codex is running, doing nothing | You have 5-15 min of turns. Run quality gates, prep verification criteria. |
| **Vague Dispatch** | "Look at this code" without scope or criteria | Wastes 10 min of codex thinking on unfocused analysis. |

## Output Format

Report to the user:
- **Codex findings** — Key results from codex's analysis or changes it made
- **Gate results** — Test/lint/build outcomes (pass/fail with details on failures)
- **Decision** — Iterating (with what follow-up) or shipping (with summary of what landed)

## Sandbox Modes

| Flag | Use |
|------|-----|
| `-s read-only` | Analysis, reviews, research (no file writes) |
| `--full-auto` | Implementation, fixes (sandboxed writes) |
| (default) | Normal with approval prompts |

## Reference

For full command reference, flags, config keys, and environment variables, see [references/commands.md](references/commands.md).
