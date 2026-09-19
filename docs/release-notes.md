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
