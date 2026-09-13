# Background credentials

The PR and Claude usage status helpers use `background_auth.py` to read saved credentials without opening macOS password dialogs. Keychain access runs in a child process with user interaction disabled and a three-second deadline. Existing GitHub environment or configuration credentials take precedence over Keychain. GitHub requests receive the token through their process environment, so `gh` does not start an interactive keychain read. Tokens are not written to the status caches.

The reader uses the current Python interpreter's Keychain identity. If Keychain has only authorized `/usr/bin/security`, the status line shows `PR: keychain unavailable` or `Fable: keychain unavailable`. To authorize the reader in the foreground, run:

```sh
python3 ~/Code/dotfiles/scripts/lib/background_auth.py authorize
```

The command skips credentials that are already available, requests access to the remaining GitHub or Claude credential items, and prints only readiness messages. Choose **Always Allow** in each macOS prompt to permit future reads by that Python interpreter. A Python update can require approval again. The background helpers never request this approval themselves.

PR lookup failures and Claude usage failures are cached for 60 seconds. The next refresh retries without a dialog. Provider-supplied five-hour and seven-day usage remain visible when the separate Fable usage request is unavailable. `login required` means credentials are missing, expired, or rejected; `fetch failed` means the request failed for another reason.

Run focused checks with `python3 tests/background_helpers.py`.
