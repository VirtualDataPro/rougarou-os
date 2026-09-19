# Bash defaults

Rougarou configures interactive login and non-login Bash with the requested
aliases and the Codex keyboard setting. Local console sessions use a plain
Bash `user@host:path$` prompt. Ordinary users get Starship in interactive SSH
sessions (`SSH_CONNECTION` or `SSH_TTY` present), except on `linux` and `dumb`
terminals, which keep the plain prompt. Root always uses a plain red
`user@host:path#` prompt. Bash remains the shell in both cases; Starship only
renders the SSH prompt. The shipped Rougarou theme uses `` as its OS symbol;
a font with that glyph belongs on the connecting terminal.

The shared defaults live in `/usr/share/rougarou/bashrc`. An explicit setup
helper adds a marked block to `~/.bashrc` that exports
`CODEX_TUI_DISABLE_KEYBOARD_ENHANCEMENT=1` and loads that file. Login shells also
load it through `/etc/profile.d/rougarou.sh`. Loading it again after Debian's
default prompt setup reapplies the appropriate prompt. Noninteractive shells
remain quiet.
See [terminal input](terminal-input.md) for the Codex setting's scope.

Fresh installation configures root, the installed operators and `/etc/skel` for
future users. Package upgrades update the shared defaults but do not rewrite
existing home files. To opt an existing account into the defaults, run this as
that account:

```sh
/usr/lib/rougarou-system/configure-shell
```

The helper preserves all lines outside its marked block and writes a mode-0600
backup beside any existing file it changes, named
`.bashrc.rougarou-backup-<unique suffix>`. Repeating setup does not duplicate
the block or create another backup when the file is unchanged. Put personal
alias or prompt overrides below the block. Symlinks, ambiguous markers and
unsafe ownership are refused rather than replaced. Open a fresh Bash session
after setup, or source your `.bashrc` in the current interactive Bash.

Missing `${XDG_CONFIG_HOME:-~/.config}/starship.toml` is seeded from the theme;
existing configurations and explicit `STARSHIP_CONFIG` settings are respected.
Missing Herdr configuration is seeded with `version_check = false` and
`manifest_check = false` in its `[update]` table, so fresh installations do not
perform those upstream checks. Existing Herdr configuration is preserved, and
Herdr's own `HERDR_CONFIG_PATH` override remains available. No service is
enabled by shell setup.

Root can explicitly prepare future-user defaults with
`/usr/lib/rougarou-system/configure-shell --skel`. This operates on `/etc/skel`,
backing up its existing `.bashrc`; it does not revisit existing accounts.

For earlier local shell configurations, the optional `--remove-legacy-inputrc` flag backs
up `~/.inputrc` before deleting only the four complete empty-macro bindings for
`\e[100;5:3u`, `\e[100;69:3u`, `\e[100;133:3u` and `\e[100;197:3u`. Other bindings,
comments and editing settings remain intact. The package migration removes the
obsolete `/etc/profile.d/rougarou-readline.sh` conffile; dpkg preserves an
operator-modified copy as `.dpkg-bak`. It is no longer loaded automatically.

| Alias | Command |
| --- | --- |
| `holdmybeer` | `sudo su -` |
| `..`, `...`, `....` | `cd ..`, `cd ../..`, `cd ../../..` |
| `d` | `docker` |
| `ff` | `fzf --preview 'bat --style=numbers --color=always {}'` |
| `g` | `git` |
| `gcad` | `git commit -a --amend` |
| `gcam` | `git commit -a -m` |
| `gcm` | `git commit -m` |
| `ls` | `eza -lh --group-directories-first --icons=auto` |
| `lsa` | `ls -a` |
| `lt` | `eza --tree --level=2 --long --icons --git` |
| `lta` | `lt -a` |

Existing `a`, `ai`, `cy` and `t` launchers remain available. The packaged `bat`
wrapper passes arguments directly to Debian's `batcat` executable.
