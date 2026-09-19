# Design decisions and open work

Rougarou OS is an **alpha Linux distribution built with AI assistance for
terminal-first server operation**. Debian supplies the kernel, core userspace,
package ecosystem and installer. Rougarou supplies the curated environment,
onboarding and agent integration. We make no claim to be the first AI-built OS.

## A stable base with separately maintained agents

Debian 13 stable is the base. We retain Debian release information for support
and package compatibility, use Debian security repositories, and keep desktop
environments and display servers out of the default image.
[Debian 13 installation guide](https://www.debian.org/releases/stable/amd64/)

Agent CLIs change faster than a server operating system. Treat their versions,
checksums and compatibility as a separate release concern. A release should
record the agent version it tested, preserve a working fallback, and avoid
silently replacing a running agent. Updating an agent is not evidence that its
new command-line flags or authentication flow still work.

## Reuse the installer; own the integration

The initial build remasters Debian netinst and adds a local package repository
plus target-configuration hook. A themed native text frontend adds
welcome and explicit operator-access choice. This retains a mature partitioner and allows
the build to avoid host block devices. The normal interactive install leaves
disk selection and destructive confirmation to the operator.

ISO reconstruction must preserve the upstream boot payloads. Xorriso's boot
replay support can retain recognizable BIOS/EFI boot structures, but successful
reconstruction is not proof of successful booting.
[Xorriso boot replay documentation](https://manpages.debian.org/trixie/xorriso/xorriso.1.en.html)

An alternative is Debian live-build with its live installer, which copies a
prepared live filesystem to the target. It needs a suitable privileged build
environment. Rootless Docker's capabilities remain scoped to its user
namespace, so a privileged container does not establish that every mount or
device operation needed by live-build will work.
[Debian Live manual](https://live-team.pages.debian.net/live-manual/html/live-manual.en.html),
[Docker rootless limitations](https://docs.docker.com/engine/security/rootless/troubleshoot/)

## Operator authority and recovery

Agents run as the signed-in operator. Supported launchers use native automatic
permission modes matching the current Omarchy approach. The installer offers
Headless operator mode, granting the named owner `NOPASSWD: ALL` after a visible
choice, and Password sudo mode. Headless mode gives every program running as
that owner full root authority through sudo; it is intended for an operator's
dedicated server. Upgrades never silently add this grant. The optional queued
worker retains `NoNewPrivileges=yes`, blocking direct sudo/setuid elevation
within its process tree. This does not isolate the owner's account or user
service manager.
See [operator access](operator-access.md) and [managed jobs](managed-jobs.md).

Keep ordinary SSH, local/serial console login and package tools available even
if onboarding fails or an AI service is offline. AI assistance must not become
the only way to administer or recover the machine. Provider credentials stay
with the operator; they are not part of an image, test fixture or release log.

## Server obstacles and how to address them

| Missing decision or obstacle | Approach |
| --- | --- |
| Encrypted disks block unattended restart | Choose console unlock deliberately. Add TPM/network unlock only after defining recovery keys, clone behavior and failure handling. |
| Lost remote access after changes | Keep Proxmox serial/console recovery, test SSH keys before disabling password access, and verify network changes from a second session. |
| Provider outage or exhausted quota | Keep the shell independent of AI; permit switching agents/providers and document which features need connectivity. |
| Local models need RAM, disk and often accelerators | Keep model weights optional. Test a supported inference endpoint separately from the base OS and publish measured requirements. |
| Snapshot mistaken for backup | Use snapshots for short-term rollback; keep independent backups and test restores. Define which application data must be quiesced. |
| Installed server drifts from the ISO | Version Rougarou configuration, record migrations, back up operator configuration, and test upgrades as well as fresh installs. |
| Fast agent releases break onboarding | Pin tested artifacts, retain checksums, test authentication and launch behavior, and provide a documented update path. |
| Multi-user servers differ from a personal workstation | Keep credentials and agent state per user; define service accounts and filesystem ownership before adding shared automation. |
| Cloned images duplicate identity | Regenerate SSH host keys and machine identity in a dedicated template workflow; do not promise clone readiness merely because ISO installs work. |
| Automated agents modify production services | Separate development and production authority, keep change records, and require explicit operational policy for any unattended service agent. |

These are product and operational decisions, not reasons to block creating the
alpha. The first release should describe its actual tested boundaries.

## Provenance, distribution and maintenance

- Verify the upstream ISO and downloaded packages before remastering. Retain
  upstream licenses and notices, and inventory added binaries and packages.
- Publish source, build instructions, artifact SHA-256 checksums and validation
  results together. A checksum detects corruption; it does not authenticate
  the publisher unless obtained through a trusted channel.
- Use a project-controlled release-signing key before claiming authenticated
  releases. Never embed signing keys or provider credentials in CI artifacts.
- Record package versions, repository state, base ISO digest, build-tool
  versions and external binary digests. A repeatable script is not a claim of
  bit-for-bit reproducibility; prove that with independent rebuilds.
- Plan security update ownership, a supported version policy, agent update
  cadence, rollback instructions and a vulnerability-reporting contact before
  inviting production use.
- Keep a disposable VM acceptance suite. Include offline installation,
  installed-system reboot, SSH, serial console, agent launch and upgrade
  behavior. Add encrypted/free-space/firmware variants before advertising them.

Public source and public binary artifacts have separate release gates. Complete
required source retention, privacy review, key rotation/recovery procedures,
tested hardware support and a maintenance commitment need explicit ownership. See [installer parity](installer-parity.md) for the
specific features retained or deferred.
