# Kanata

`kanata-setup` installs Homebrew's Kanata with the Karabiner driver package that
Kanata release supports, without Karabiner-Elements. `install.sh` runs it; run
it alone after a Kanata upgrade. `kanata-setup --dry-run` shows what it would do.
Grant Input Monitoring and Accessibility to `~/.local/bin/kanata`.

## Train game keyboard lock

Hold both Shift keys, then press K to enter kid mode; it plays three Tinks. Hold
either Shift and press K to unlock; it plays three Pops. In kid mode, ordinary
letters, numbers, and punctuation type normally; brightness, volume, Left/Right,
Space, Return, Backspace, and Command-Backspace work. Other modifier shortcuts,
Fn, system function keys, Escape, and Tab stay disabled.

The `com.tylerlaprade.kid-trackpad` launch agent runs `scripts/kid_trackpad.py`,
which follows Kanata's layer over the TCP port the Kanata daemon opens on
`127.0.0.1:41471`. While kid mode is on, an event tap drops pointer movement,
clicks, scrolling, and trackpad gestures, including Spaces and Mission Control
swipes. Keyboard and media keys never reach the tap. Unlocking turns the tap off,
and the tap ends with the agent, so a crash cannot leave the trackpad blocked.
Neither Mouse Keys nor “Ignore built-in trackpad when mouse or wireless trackpad
is present” is involved. Leave that setting off: any mouse, including a virtual
one, would then disable the trackpad even while unlocked.

The tap is an agent, not part of the root Kanata daemon, because macOS 15 does
not let a launch daemon create it. It runs on Apple's `/usr/bin/python3`, which
needs Accessibility once: allow `python3` in System Settings → Privacy &
Security → Accessibility. Until then the agent logs the missing permission to
`/tmp/kid-trackpad.log` and retries every ten seconds.

Kanata's built-in Left Control + Space + Escape emergency exit still works.
Kanata reads that chord before remapping, so a layer cannot disable it.
