# Rougarou OS

**AI-built. Operator-owned.** A headless Debian stable derivative for agentic
server work, starting with Proxmox virtual machines.

Rougarou brings Omarchy's terminal workflow and guided setup to a server: a real
shell, an inline AI agent, GitHub onboarding and a restrained bayou-green
identity. Linux and Debian remain the foundation; no desktop environment is
installed.

**0.1.0-alpha.8** is the first public alpha. Download the signed
[installer ISO or cloud image](https://github.com/VirtualDataPro/rougarou-os/releases/tag/v0.1.0-alpha.8),
then follow the [verification guide](docs/downloads.md). See
[release notes](docs/release-notes.md) and [tested configurations](docs/validation.md)
before installing. This is experimental software for evaluation in a separate VM.

## What ships

- Debian 13 (trixie), amd64, a themed native text installer and BIOS/UEFI boot.
- Operator-selected storage, encryption, networking, account and sudo authority.
- A separate key-based cloud-init image workflow with disk growth and host-side
  Proxmox recovery tools.
- Offline core dependencies and selectable pinned Codex or Gemini CLI packages.
- Explicit verified first-login downloads for OpenCode and Claude Code.
- OpenSSH, QEMU guest agent, tmux, Git, GitHub CLI and terminal server tools.
- First-login setup for Git identity, GitHub and an AI provider.
- Inline agents using native approval modes and explicit operator authority.
- Optional managed jobs, a systemd user worker and local health checks.
- Job acknowledgment that clears MOTD attention while preserving outcomes/logs.
- Reviewed signed updates, recovery references and encrypted Restic backups.
- Optional webhook notifications and a terminal control menu.
- A responsive wolf welcome screen, plain Bash console, SSH Starship and red
  root prompt.
- Optional rootless Docker, system Docker, Podman and Herdr workspaces.
- A RougarouOS text boot menu using Debian's native kernel and recovery entries.

The installer preselects **Rootless Docker**. The agent defaults to None / bring
your own; Podman and Herdr default to No. Choose No Docker or System Docker if
preferred. Rootless dependencies install offline; start the user service when
ready with `rougarou-docker setup`.

Cloud credentials, AI subscriptions and model weights are not included.
Authentication and remote inference need the selected service. Rougarou remains
usable without an AI provider. The normal installer leaves disk selection and
destructive partition confirmation to the operator.

## Install in Proxmox

Start with **2 vCPUs, 4 GiB RAM and a 32 GiB virtual disk** for cloud-assisted
work; local models need separate sizing. Verify the image's signed checksums,
upload it and attach it to a new VM.
Use VirtIO SCSI and VirtIO networking, enable QEMU Guest Agent, and use
OVMF/UEFI with an EFI disk. Disable Secure Boot until release-specific testing
establishes support.

Follow the [first installation guide](docs/first-test.md) and
[Proxmox guide](docs/proxmox.md), or the separate
[cloud image guide](docs/cloud-image.md). After installation:

```sh
rougarou setup       # Repeat or resume operator setup
rougarou ai          # Selected inline agent; aliases: a and ai
rougarou doctor      # Local health and configuration checks
rougarou menu        # Agents, jobs, updates, backups and notifications
rougarou jobs ack    # Acknowledge unsuccessful runs; keep their logs
```

The Codex shortcut is `cy`. Headless operator mode grants the named owner
passwordless sudo; Password sudo mode remains available. Agent approval review
is separate from this system authority. The optional worker runs as the owner;
`NoNewPrivileges` blocks direct sudo/setuid elevation within its process tree,
but does not isolate the owner's account or user service manager. See
[operator access](docs/operator-access.md) and [managed jobs](docs/managed-jobs.md).

Guides: [providers](docs/providers.md), [shell defaults](docs/shell-defaults.md),
[terminal input](docs/terminal-input.md), [containers](docs/containers.md),
[guided updates](docs/guided-updates.md), [backups](docs/backups.md),
[notifications](docs/notifications.md), and [menu](docs/menu.md).

## Build and verify

The ISO build uses an isolated Debian container and ordinary files, without
host block devices, loop mounts or a privileged container. See
[build documentation](docs/build.md) for signing and provenance requirements.

```sh
python3 -m unittest discover -s tests -v
scripts/check.sh
scripts/build-iso.sh  # Requires an external signing identity; see build docs
```

The release includes source archives, dependency notices and signed build
manifests; see [source materials](docs/downloads.md#source-materials).
Keep generated images, VM disks, package caches, credentials and private keys
out of source control. CI scans Git history for secrets and the source tree for
private deployment details. Image releases also require filesystem and
archive-metadata review.

## Scope

Rougarou is an independent Debian derivative inspired by Omarchy. It retains
Debian's installer and supported kernel; it is not an Omarchy fork or a new
Linux kernel. See [installer parity](docs/installer-parity.md),
[design decisions](docs/design-decisions.md), and the
[kernel assessment](docs/kernel-decision.md).

Updates use a signed testing-to-stable promotion process. Direct Debian
security updates remain an explicit exception. The image carries its signed
bootstrap repository; **no public Rougarou APT endpoint is provisioned**.
Configure only a reviewed host and channel you operate or trust. See
[updates](docs/updates.md) and [self-hosting](deployment/README.md).

Original Rougarou integration code is MIT licensed. Debian and bundled upstream
software retain their respective licenses; see [NOTICE](NOTICE). Rougarou OS is
not endorsed by Debian, Omarchy, OpenAI, GitHub or Proxmox.
