# Second Mac

Both machines stay in use, so nothing is wiped and nothing is zipped. The
repo carries the configuration; each machine logs in on its own.

1. Clone this repo to `~/Code/dotfiles` and run `install.sh`. It installs the
   tools, links the configs, applies `/etc/hosts` from `scripts/setup/hosts`,
   loads Kanata and the user agents, and runs the three-way sync for the
   shared macOS defaults, login items, browser Local State, Helix, and
   Graphite settings. A machine that has no base adopts the repo and does not
   write its own defaults back over it; one that has a base keeps its live
   changes. VS Code is not installed. `~/.gitconfig` stays on the machine and
   includes the shared file, so the CodeRabbit machine id is not shared.
   The daily sync only links config. Run `install.sh` again when the tool
   list changes; steps that are already done stop.
2. Log in: `gh auth login`, `claude`, `codex`, `gemini`, `grok`, `gt auth`,
   `sourcery login`, `aws configure`.
3. Keys: create a new SSH key on the machine and add it to GitHub before
   cloning anything else. The shared gitconfig rewrites GitHub HTTPS to SSH.
   Export the GPG signing key from the first Mac (`gpg --export-secret-keys`)
   and import it, so commits sign as the same key.
4. Code signing: on the first Mac, Xcode > Settings > Accounts > Export Apple
   ID and Code Signing Assets; open the file on the second Mac.
5. Grant Input Monitoring and Accessibility to `~/.local/bin/kanata`, and
   Accessibility to Ghostty, AltTab, and Homerow when they ask.
