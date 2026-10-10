# Claude Code only

Rules that name Claude's own tools or models. The instructions every
harness shares live in `~/.agents/AGENTS.md`, which must stay free of
anything harness-specific.

- Never recommend Opus 5 (`claude-opus-5`) for any role, main session or subagent ("Opus 5 is the worst model I've ever talked to," 2026-10-09). A metric that favors it is a data point, never an option.
- The Claude in Chrome tab group runs in a hidden window: `requestAnimationFrame` never fires and timers throttle to about once a minute after five minutes, while screenshots, DOM reads and WebSocket traffic still work. Use it for stills and state only. Verify anything driven by the frame clock (fades, camera moves, clocks) in headless Chrome or Playwright, and keep each JavaScript evaluation to a few seconds.
- A draft saved through the claude.ai Gmail connector stores every link as a `google.com/url` redirect that expires in about a day. Fix the links in Gmail's own compose window, then re-read the draft to confirm none remain.
- To spawn a peer Claude session, pass `claude` itself as the terminal's command. A wrapper script makes it a subprocess, which turns its transcript off. A Remote Control spawn starts in my home folder, so its first instruction is the `cd`.
- `git checkout`, `git restore`, `git reset`, `git stash`, and `git clean` are denied in `settings.json`, because several sessions share each working tree. A denial on one of them is that rule, not my answer to the step; switch branches with `git switch`.
