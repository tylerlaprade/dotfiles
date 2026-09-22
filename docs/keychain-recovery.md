# Keychain recovery

The September 21, 2026 prompt storm remains partly unresolved. The status helpers
skip credential reads when the default keychain is locked, and Claude usage
failures stay in the statusline and event log without desktop notifications.
These changes do not repair macOS's security service or alter Claude's own reads.

The Mac is running macOS 15.7.5 (24G624). Five `securityd` crashes between
9:07 and 9:12 PM followed `Probable bug: error destroying Mutex: 16` and an
uncaught C++ exception. Matching crash reports are under
`/Library/Logs/DiagnosticReports/securityd-2026-09-21-*.ips`. The service also
reported code-identity error `-67065` for `/usr/bin/security`; that executable's
on-disk signature verified successfully. This does not establish a bad password
or a missing `apple-tool:` grant. The logged credential ACL already includes it.

Sequoia 15.8 (24H23) is offered by Software Update. Its release notes do not
establish that this particular crash is fixed. Download attempts stalled in the
update service's synchronous scan, including a retry with `--no-scan`.
Installing the update requires administrator authentication and a restart.
Claude Code's updater reports that 2.1.278 is current.

After the system update and restart, verify the running macOS version, actual
Claude and GitHub credential reads, and whether the same `securityd` crash
recurs. Re-run `tests/background_helpers.py` and `tests/statusline.py` then.
The original 35 tests passed before the machine slowed down; a later run hit
five-second subprocess timeouts. The notification regression passed separately
with a 60-second deadline. Retire this recovery note once the system failure is
resolved and verified.
