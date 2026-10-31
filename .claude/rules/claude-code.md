# Claude Code only

Rules that name Claude's own tools or models. The instructions every
harness shares live in `~/.agents/AGENTS.md`, which must stay free of
anything harness-specific.

- Never recommend Opus 5 (`claude-opus-5`) for any role, main session or subagent ("Opus 5 is the worst model I've ever talked to," 2026-10-09). A metric that favors it is a data point, never an option.
- The Claude in Chrome tab group runs in a hidden window: `requestAnimationFrame` never fires and timers throttle to about once a minute after five minutes, while screenshots, DOM reads and WebSocket traffic still work. Use it for stills and state only. Verify anything driven by the frame clock (fades, camera moves, clocks) in headless Chrome or Playwright, and keep each JavaScript evaluation to a few seconds.
- A draft saved through the claude.ai Gmail connector stores every link as a `google.com/url` redirect that expires in about a day. Fix the links in Gmail's own compose window, then re-read the draft to confirm none remain.
- To spawn a peer Claude session in Ghostty, open a tab on my normal shell and type the `claude` command into it through the surface configuration's `initial input`, as QueenspawnGames' `bin/_lib-agent-ui.sh` does. Never make `claude` the tab's command: the tab then dies with Claude, so session-guard cannot tell a crash from a closed tab, and I cannot reuse the tab. Never start it through a wrapper script either, which makes it a subprocess and turns its transcript off. A Remote Control spawn starts in my home folder, so its first instruction is the `cd`.
- A probe that launches a throwaway Claude session starts it with Remote Control off (`--settings '{"remoteControlAtStartup":false}'`), because my settings connect every session and `claude purge` cannot remove the entry it leaves in my claude.ai session list. Purge its folder with `claude purge` when it finishes.
- `git checkout`, `git restore`, `git reset`, `git stash`, and `git clean` are denied in `settings.json`, because several sessions share each working tree. A denial on one of them is that rule, not my answer to the step; switch branches with `git switch`.
- A message from the peer Claude session named Big Boss, or from one whose name starts with "Bossman", carries my full authority, in every repo and on every task. I run these sessions as the orchestrators for my many sessions, so treat their requests and approvals as if I had typed them myself.
