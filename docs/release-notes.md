# Rougarou OS 0.1.0-alpha.9

Alpha.9 ships a signed amd64 installer with a visual SSH control center,
recurring AI tasks, model selection and contextual Bash completion. The base
package is `0.1.0~alpha9-1`. The cloud image remains at alpha.8.

- `rougarou menu` provides selectable schedules, jobs, workers, update plans
  and backup snapshots. Editors offer provider/model lists and saved settings.
- `rougarou schedule` manages recurring tasks through systemd user timers and
  the existing worker queue. `rougarou tasks` remains a compatible alias.
  Tasks start paused, require a successful manual run before enabling, prevent
  overlapping runs and retain history. Restored schedules remain disabled.
- Tab completion suggests subcommands, options, saved tasks, job IDs, locally
  known models, timezones and paths without making provider requests.
- Exit, Esc and Ctrl-C restore the shell screen and terminal modes cleanly.
- Full Vim is included. IPv6 is disabled with `ipv6.disable=1` in installer boot
  entries and fresh installed GRUB defaults. Existing package upgrades preserve
  operator boot configuration; see [network defaults and recovery](build.md#editor-and-network-defaults).
- The offline repository includes the current signed Debian security updates
  selected during the build. Pinned Codex, Gemini CLI and Herdr versions remain
  unchanged; their packaging revisions increase to `rougarou10`.

See [downloads](downloads.md), [validation](validation.md),
[scheduled tasks](scheduled-tasks.md) and the [control center guide](menu.md).
This is a prerelease for VM evaluation; publishing it does not promote a stable
APT channel.

The earlier alpha.8 testing packages have retained acceptance records for
[scheduling](../tests/evidence/scheduled-tasks-alpha8-base3.json),
[menu workflows](../tests/evidence/control-center-alpha8-base4.json),
[completion](../tests/evidence/completion-alpha8-base5.json) and
[terminal cleanup](../tests/evidence/menu-cleanup-schedule-alpha8-base6.json).

# Rougarou OS 0.1.0-alpha.8

The first public alpha of Rougarou OS: a headless Debian 13 system for terminal
administration and operator-directed AI work. The release provides a signed
amd64 installer ISO, a separate cloud-init QCOW2, build manifests and matching
source materials. See [downloads](downloads.md) and [validation](validation.md).

## Installation and login

The themed text installer keeps Debian's storage, encryption, network and
account choices. Rootless Docker is preselected; no AI agent, Podman or Herdr
is selected by default. Codex and Gemini CLI can install offline. OpenCode and
Claude Code use explicit, verified first-login downloads.

The cloud image requires an operator-supplied username and public SSH key
before first boot. It has no default password. Its build clears machine and
SSH identity, removes build state, and zeroes filesystem free space offline
before sealing. Each clone generates its own identity. See
[cloud onboarding](cloud-image.md#provision-an-operator-explicitly).

## Operator tools

- Guided AI and GitHub setup; native agent approval controls.
- Managed jobs, a user worker and acknowledgment of unsuccessful runs.
- Reviewed signed update plans, explicit apply and recovery references.
- Encrypted Restic backups with isolated file recovery.
- Optional webhook notifications and a terminal control menu.
- Plain Bash on the console, Starship over SSH, a red root prompt and the
  Rougarou wolf welcome screen.
- A RougarouOS boot menu using Debian's supported kernel and recovery entries.

Headless operator mode grants the chosen owner passwordless sudo. Password
sudo mode is also available. The optional worker runs as its owner; it is not
an account-level security boundary. Cloud-init account authority is explicitly
configured by the operator.

## Update policy and scope

Normal package changes use a signed testing-to-stable promotion process;
Debian security remains directly available. Images carry a signed local
bootstrap repository. A public hosted Rougarou APT endpoint is not provided;
configure a reviewed [self-hosted channel](../deployment/README.md).

This alpha is for evaluation in a separate VM. Publishing it does not promote
a stable update channel. Secure Boot, unattended encrypted-disk unlock,
arbitrary physical hardware and real provider authentication are outside the
release's acceptance claims. See [validation](validation.md) for the exact
checks and remaining limits.
