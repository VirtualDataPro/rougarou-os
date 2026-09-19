# Installer parity and alpha scope

Rougarou OS 0.1 is an **alpha Debian 13 derivative**. It uses Debian's text
installer with a themed native frontend, Rougarou welcome/access screens and
bundled packages. This is a mapping of
the intended install experience, not a claim that every installer path has
passed acceptance testing. Consult the release's validation record before use.

Omarchy's `quattro` source and linked manual are the comparison reference.
Other upstream revisions may differ; this mapping is not a compatibility
contract with every Omarchy release.

## Installation

| Operator choice | Omarchy reference | Rougarou alpha approach |
| --- | --- | --- |
| Boot from an ISO | Dedicated installer ISO | Debian netinst boot infrastructure with Rougarou branding and payload |
| Keyboard, location and timezone | Configuration wizard | Native Debian installer questions |
| Hostname and networking | Hostname; automatic networking | Native Debian networking, including DHCP and manual configuration |
| Username and password | Installer creates operator | Native Debian account setup; explicit Headless operator or Password sudo choice |
| Git name and email | Optional installer fields | Rougarou onboarding after login |
| Select an entire disk | Full-disk install | Native guided partitioning; operator confirms destructive changes |
| Use existing free space | Free-space install | Native free-space/manual partitioning; no custom partition editor |
| Encrypt storage | Encryption by default, with opt-out | Native encrypted-LVM choice; operator selects the storage method explicitly |
| Advanced storage | Opinionated layout | Debian manual partitioning and its available LVM/RAID options |
| Install without Internet | Bundled package mirror | Bundled dependency repository and local target-configuration hook |
| Console access | Desktop installation | Text console and serial console; multi-user boot target |
| Proxmox integration | ISO and documented VM setup | VirtIO-compatible Debian kernel, serial console, QEMU guest agent |

Debian's storage options are broader, but its question order, appearance,
filesystem defaults and encryption defaults are not identical to Omarchy's.
Retaining the native partitioner reduces the amount of disk-management code
Rougarou must maintain. A feature being available upstream does not establish
that the Rougarou image has tested it.

Sources: [Omarchy installation manual](https://omarchy.org/manual/getting-started/),
[Debian installer components](https://www.debian.org/releases/trixie/amd64/ch06s03.en.html),
[Omarchy ISO implementation](https://github.com/omacom/omarchy-iso/blob/quattro/README.md).

## Agent behavior

The operator chooses an agent, authenticates with the upstream provider, and
launches it in the terminal. No provider credentials belong in the ISO. Native
agent permission modes and operating-system privileges are separate boundaries.

Current Omarchy launches Codex with `--approve-for-me`, Claude with
`--permission-mode auto`, and OpenCode with `--auto`. Rougarou follows this
operator-driven model for supported launchers and adds inline-terminal behavior
where the tool supports it. The installer offers explicit named-owner passwordless
sudo mode for unattended administration, plus a password-required alternative.
This choice is independent of native agent approvals. The optional managed
worker blocks direct sudo/setuid elevation within its process tree; it does not
isolate the owner's account or user service manager. No root-equivalent
container group is added. The shell remains usable when an agent or provider
is unavailable.

Sources: [Omarchy launcher aliases](https://github.com/omacom/omarchy/blob/quattro/default/bash/aliases),
[Omarchy Docker privilege decision](https://github.com/omacom/omarchy/blob/quattro/install/config/docker.sh).

Omarchy currently creates lazy installation stubs for many agents. Rougarou's
optional bundled agents support offline installation; provider setup,
authentication and remote inference can still require Internet access. Shipping
an agent does not ship a subscription or local model weights.
[Omarchy AI manual](https://omarchy.org/manual/ai/)

## Deliberately deferred

| Feature | Alpha status and next requirement |
| --- | --- |
| Omarchy `cidata` configuration format | Not implemented. Debian preseed is a different interface; do not attach Omarchy seed files and expect compatibility. |
| Native Proxmox cloud-init onboarding | Separate [cloud image workflow](cloud-image.md); not the ISO installer or Omarchy seed schema. Each public cloud artifact needs independent sanitation and clone acceptance. |
| Install for another owner | Deferred. Ordinary installation creates the operator immediately. |
| Automatic Tailscale enrollment | Deferred. Needs explicit enrollment, protected short-lived credentials and retry/cleanup tests. |
| TPM or network-based disk unlock | Deferred. Encrypted guests require console unlock; no unattended-unlock promise. |
| Omarchy/Limine snapshot boot and restore | Not reproduced. Debian boot and recovery paths apply. |
| Verified Secure Boot support | Not a Rougarou claim until tested against the produced ISO and installed guest. |
| Full physical-hardware certification | Deferred. Initial acceptance target is an x86-64 Proxmox/QEMU VM. |

Omarchy's unattended installer accepts a labeled seed drive with configuration,
optional authorized SSH keys and optional Tailscale enrollment. These useful
fleet features need their own implementation and tests in Rougarou.
[Omarchy unattended installations](https://omarchy.org/manual/unattended-installs/)

The required release evidence is a real boot, install, reboot and login using a
disposable virtual disk. Offline, encrypted, BIOS, UEFI and free-space paths
must be recorded individually; one successful path does not verify the others.
