# Proxmox installation and operation

Use a new, disposable VM for this alpha. Suggested initial resources are 2
vCPUs, 4 GiB RAM, 32 GiB storage and an outbound internet connection for provider
authentication. The installer payload itself is designed to work offline.

1. Upload the Rougarou ISO to a storage location that accepts ISO images.
2. Create an amd64 Linux VM. Select OVMF (UEFI), add an EFI disk and leave
   pre-enrolled Secure Boot keys disabled for this alpha. SeaBIOS is an alternate
   boot path, subject to the recorded validation matrix.
3. Use VirtIO SCSI, a SCSI virtual disk with discard when storage supports it,
   and a VirtIO network adapter on your intended bridge/VLAN.
4. Enable QEMU Guest Agent in the VM's options. For terminal access, optionally
   add serial port 0 (socket) and choose the ISO's serial console boot entry.
   The Proxmox display console is also available for initial installation.
5. Boot the ISO and follow the text installer. Keep personal credentials out of
   ISO files and reusable templates. Choose a hostname and your own account.
6. Select a disk layout deliberately. Encrypted LVM requires an unlock
   passphrase at boot; the guest cannot reach SSH until its root is unlocked.
   Use the Proxmox console for unlocking this release. Unencrypted guests can
   restart unattended; secure the hypervisor and storage accordingly.
7. Eject the ISO, boot from the installed disk, and log in. Complete
   `rougarou setup`. Device login lets a separate computer supply the browser.
8. Check `rougarou doctor`, `systemctl status qemu-guest-agent`, networking and
   SSH from a second session before removing console access.

The installer does not require a GUI on the guest. Proxmox's web console is a
hypervisor feature and adds no desktop dependencies to Rougarou.

## First service

With the default Rootless Docker selection, run setup from your ordinary
operator account:

```sh
rougarou-docker setup
docker info
```

Podman is available only if selected or installed separately. See
[container setup](containers.md) for either engine. For a long-running service,
use a systemd user unit or, with Podman, Quadlet; choose a pinned image digest
and a dedicated data directory. Decide whether user lingering is appropriate
before expecting user services to run after logout.

## Recovery and maintenance

- Keep a Proxmox console route and a second SSH session during access changes.
- Install and test an SSH public key before disabling password authentication.
  ISO installations initially accept the named operator's chosen password;
  root SSH login is disabled. The separate [cloud image](cloud-image.md)
  requires a supplied public SSH key and disables password SSH authentication.
- Snapshot before OS/agent upgrades. Back up application data separately and
  test restore; a snapshot on the same failed storage is not a backup.
- Use the [guided update workflow](guided-updates.md) below during maintenance.
  Review the exact plan and verify a real recovery point before applying it.
  Rougarou disables automatic reboot.
- Codex is pinned independently of Debian. Update it through a reviewed
  Rougarou package published to your configured signed channel.
- Cloning an operator-used ISO installation requires a separate review and
  cleanup of machine identity, SSH keys and credentials. For new clones, use
  the [sealed cloud image](cloud-image.md), which clears build identity and
  requires explicit per-clone account and network configuration.

```sh
rougarou update refresh
rougarou update plan
# Create/check a real recovery point and substitute its reference below.
rougarou update apply --plan PLAN_ID --recovery-point 'YOUR_VERIFIED_RECOVERY_POINT' --confirm
rougarou update status
```

Primary reference: [Proxmox QEMU/KVM VM documentation](https://pve.proxmox.com/pve-docs/chapter-qm.html).
