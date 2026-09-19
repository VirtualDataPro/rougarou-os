# Codex keyboard compatibility

Rougarou uses Codex's environment switch for keyboard compatibility:

```sh
export CODEX_TUI_DISABLE_KEYBOARD_ENHANCEMENT=1
```

The managed section of each configured user's `~/.bashrc` exports this value.
The shared shell defaults also cover interactive Bash login sessions. New
accounts inherit the managed block from `/etc/skel/.bashrc`; existing accounts
can apply it with `/usr/lib/rougarou-system/configure-shell`. The installer
configures its owner and root. Package upgrades do not silently rewrite homes.
The helper backs up changed shell files and preserves content outside its
marked block, as well as existing user application configuration.

This setting is supported by the pinned **Codex 0.155.0** implementation. Its
[keyboard setup](https://github.com/openai/codex/blob/rust-v0.155.0/codex-rs/tui/src/tui/keyboard_modes.rs)
recognizes `1` and skips enabling enhanced keyboard reporting. The
[startup path](https://github.com/openai/codex/blob/rust-v0.155.0/codex-rs/tui/src/tui.rs)
also skips the keyboard capability query. Recheck this behavior when updating
Codex; this is a version-tested environment workaround.

Some modified-key shortcuts may behave differently because the terminal no
longer supplies the enhanced distinctions. Ordinary Bash Ctrl+D remains EOF.
To temporarily test Codex's enhanced mode for one invocation:

```sh
CODEX_TUI_DISABLE_KEYBOARD_ENHANCEMENT=0 codex
```

## Retiring earlier local filters

The package migration retires `/etc/profile.d/rougarou-readline.sh` using
Debian's conffile migration mechanism. Operator-modified copies are retained
as migration backups rather than treated as untouched defaults.

If the earlier four empty macros were also added to `~/.inputrc`, the explicit
shell helper can remove just those exact bindings, after backing up that file:

```sh
/usr/lib/rougarou-system/configure-shell --remove-legacy-inputrc
```

They are `\e[100;5:3u`, `\e[100;69:3u`, `\e[100;133:3u` and
`\e[100;197:3u`, each mapped to an empty string. Other bindings and settings
remain intact. No terminal input is flushed and no replacement Readline
filters are installed. Already-running Bash processes retain their in-memory
bindings until exited; open a fresh terminal login after migration.

## Validation

The [real Codex startup check](../tests/vm/codex-keyboard-check.py) runs the
pinned binary twice in isolated PTYs with empty state and no credentials.
In a network-disabled Debian environment, the control run (`0`) emitted one
keyboard-enhancement enable sequence and one query; the disabled run (`1`)
emitted neither. It submits no prompt and makes no model request. This checks
the switch's operation, not the original terminal application's delay.

The [release validation](validation.md) records the status of fresh-install and
shell-startup checks for the public artifact. This does not claim that the
original terminal delay was reproduced with a live provider session.
