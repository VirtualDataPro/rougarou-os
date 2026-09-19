# First installation

Use a separate disposable VM for an alpha installation. Start with the
[validation record](validation.md): download only an accepted release with its
signed checksums, public signing fingerprint and matching source materials.
Use the [download verification guide](downloads.md), or see
[build instructions](build.md) to build your own.

## Install from ISO

1. Create a new amd64 Proxmox VM with 2 vCPUs, 4 GiB RAM and a 32 GiB disposable
   disk. Enable QEMU Guest Agent and use VirtIO storage/networking. OVMF/UEFI
   with an EFI disk is the recommended starting point; disable Secure Boot.
2. Verify and attach the release ISO, then choose the text installer.
3. Set language, keyboard, hostname, networking, timezone and your account.
4. Choose **Headless operator** or **Password sudo**. Headless operator grants
   the named account full root authority through passwordless sudo.
5. Review the software choices. **Rootless Docker** is preselected; the agent
   is None / bring your own, and Podman and Herdr are No.
6. Select the disposable disk and confirm the intended layout. Encrypted LVM
   requires a console passphrase at boot; unattended unlock is not provided.
7. Finish installation, eject the ISO and reboot into terminal onboarding.
   Optional provider and GitHub setup can be skipped and resumed later.

| Choice | What to expect |
| --- | --- |
| None / bring your own agent | No agent package; the shell remains usable |
| Codex or Gemini CLI | CLI and dependencies installed offline; authenticate after login |
| OpenCode or Claude Code | Choice recorded; a separate verified download needs explicit confirmation and networking |
| No Docker | No Docker engine installed |
| Rootless Docker | Dependencies installed; run `rougarou-docker setup` as the operator when ready |
| System Docker | Root-owned daemon; use `sudo docker`, with no Docker-group grant |
| Podman | Separate optional engine with rootless dependencies |
| Herdr | Optional terminal workspace, started manually with `herdr` |

The settings record `/etc/rougarou/install-software` contains the completed
choices. It is not a command. See [provider setup](providers.md),
[container setup](containers.md) and [Proxmox installation](proxmox.md).
The separate [cloud image route](cloud-image.md) uses your public SSH key and
cloud-init network settings; disable Proxmox's automatic package-upgrade option
before its first boot.

## First login

```sh
rougarou version
rougarou access status
rougarou status
rougarou doctor
rougarou setup
rougarou menu
```

Use `a` or `rougarou ai` to launch the selected agent inline. No agent is
required for administration. Console sessions use plain Bash; SSH sessions use
Starship, and the root prompt is red. See [shell defaults](shell-defaults.md)
and [keyboard compatibility](terminal-input.md).

Managed jobs, backups, notifications and rootless Docker are explicitly set up
by the operator; they are not automatically started for a fresh account.
After reviewing unsuccessful jobs, `rougarou jobs ack` clears their MOTD
attention count without deleting the outcome or logs. See [jobs](managed-jobs.md).

## Updates and recovery

The installed signed bootstrap repository is local. Direct Debian security
updates require working outbound access. **There is no public Rougarou APT
endpoint configured by this project.** Add a reviewed self-hosted testing or
stable source only after verifying its signing key and publication policy;
never copy an example URL as a working service. See [updates](updates.md) and
[repository hosting](../deployment/README.md).

```sh
rougarou update refresh
rougarou update plan
# Review the exact plan and create/check a real recovery point first.
rougarou update apply --plan PLAN_ID --recovery-point 'YOUR_VERIFIED_RECOVERY_POINT' --confirm
```

The [guided updater](guided-updates.md) requires an explicit recovery reference;
it cannot prove that an operator-attested reference is restorable. Keep a
separate data backup, test restoration, and preserve console access. A
hypervisor snapshot alone is not an independent backup.

Before relying on an alpha VM, test reboot, SSH, guest-agent access, provider
login if selected, your service workload and encrypted boot if selected. Read
[known validation limits](validation.md); a passing generic install does not
establish support for every storage layout or hardware configuration.
