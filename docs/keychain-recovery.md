# Keychain recovery

The September 21, 2026 prompt storm remains partly unresolved. The status helpers
skip credential reads when the default keychain is locked or its status probe
does not finish within two seconds. Claude usage also stops a credential read
after five seconds and preserves cached usage when a refresh fails. The statusline
marks cached Fable usage as stale until a later refresh succeeds. These changes
do not alter Claude's own Keychain reads.

The Mac was running macOS 15.7.5 (24G624) during the original incident. Five
`securityd` crashes between 9:07 and 9:12 PM followed
`Probable bug: error destroying Mutex: 16` and an
uncaught C++ exception. Matching crash reports are under
`/Library/Logs/DiagnosticReports/securityd-2026-09-21-*.ips`. The service also
reported code-identity error `-67065` for `/usr/bin/security`; that executable's
on-disk signature verified successfully. This does not establish a bad password
or a missing `apple-tool:` grant. The logged credential ACL already includes it.

At the time, Sequoia 15.8 (24H23) was offered by Software Update. Its release
notes did not establish that this particular crash was fixed. Download attempts
stalled in the update service's synchronous scan, including a retry with
`--no-scan`.
Claude Code's updater then reported that 2.1.278 was current.

On September 25, macOS 15.8 (24H23) and Claude Code 2.1.282 were running.
The Claude usage cache briefly reported `keychain unavailable`, then recovered
without a login change. The precise failing Keychain operation was not captured.
Check the usage cache, `~/.claude/acl-events.log`, and recent `securityd` crash
reports if this recurs. Retire this recovery note once the system failure is
resolved and verified.
