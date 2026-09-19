# Operator access

The installer offers two explicit choices for the named account being created:

- **Headless operator**: that account can run any command as any user through
  sudo without entering a password. Every program running as the account can
  gain full root authority. This is the offered default for agentic server work.
- **Password sudo**: Rougarou adds no passwordless grant. Debian's normal sudo
  authentication applies unless an administrator has configured separate rules.

The choice appears after account creation settings and before storage setup.
Only the selected install owner receives the headless grant. Other accounts are
not included. Interactive AI still starts as the regular account, with the
selected tool's native sandbox and approval behavior; it is not launched as root.

Review or change an existing installation from the operator's shell:

```sh
rougarou access
rougarou access --json
rougarou access configure
rougarou access headless
rougarou access password
```

Enabling headless mode explains the authority and requires confirmation. The
initial change may need one ordinary sudo password. For an explicitly authorized
noninteractive change, `rougarou access headless --acknowledge-root-access` uses
noninteractive sudo and fails if current policy requires a password. Package
upgrades never select a mode or add privileges automatically. Onboarding can
offer a review for an older installation without changing its current policy.

The managed rule is `/etc/sudoers.d/90-rougarou-owner` (root-owned, mode 0440).
The nonsecret mode record is `/etc/rougarou/operator-access.json` (root-owned,
mode 0644). The helper checks the local username and UID, validates the candidate
and complete sudo policy with visudo, atomically replaces the managed rule,
and rolls it back if validation or state writing fails. It refuses symlinks,
unknown content at its managed paths, and silent transfers to another owner.

Password mode removes only Rougarou's own rule. It preserves manually configured
sudo policies, including an older operator's manual NOPASSWD rule. Status runs
`sudo -n -k -- /usr/bin/true`, which ignores cached authentication for that command
without invalidating it. A successful probe in password mode indicates another
rule permits that command without a password; review `sudo -l` to identify it.
That single probe does not imply an external rule allows every command.

The optional [managed worker](managed-jobs.md) uses `NoNewPrivileges=yes`.
This blocks direct exec-based sudo/setuid elevation in the service and its
descendants, even when the account has headless access. It does not isolate
the owner's account: a same-user program can request work from the user service
manager or modify owner startup files outside that process tree.
Batch Codex uses noninteractive approval behavior and the
selected read-only or workspace-write sandbox. Administrative tasks that need
root belong in an explicitly authorized interactive session.

## Validation

`tests/test_operator_access.py` exercises named-owner scope, acknowledgement,
invalid accounts, symlink and permission checks, preservation of custom rules,
candidate/full-policy failures, rollback and public status reads. CLI tests
check explicit consent, fixed argv, the real account identity rather than
environment variables, and unchanged AI execution authority.

`tests/vm/operator-access-container.py` is an opt-in integration test for a
disposable Debian container. It exercises real sudo/visudo, a second operator,
nonroot AI execution, mode changes, and NoNewPrivileges. It refuses to run
outside a root container. The test does not run a model API request or alter
the development host's sudo policy.
