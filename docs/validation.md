# Release validation

Release: **0.1.0-alpha.9**. The signed manifest and checksum list identify the
exact installer and source tree. The separate cloud image remains alpha.8;
its earlier results are in [alpha.8 validation](validation-alpha8.md).

## Installer ISO

The released ISO has SHA256 `a12e573d50371af2622155b11f16973e6416a544195b32579ca2ccec7d9041a2`.
Signed-media verification, the offline package audit and all 102 recorded build
input checks passed. Fresh package install/remove/purge and actual alpha.8 to
alpha.9 upgrades passed, preserving operator homes and both unchanged and
modified configuration. [Package evidence](../tests/evidence/packages-alpha9.json)
records the checks.

The exact final ISO passed original-media BIOS/UEFI boots and three fresh
offline installations:

| Firmware | Agent | Docker | Podman / Herdr | Installation time |
| --- | --- | --- | --- | --- |
| BIOS | None | Rootless | Neither | 503 seconds |
| UEFI | Codex | Rootless | Both | 530 seconds |
| BIOS | Gemini CLI | System | Neither | 252 seconds |

All nine checks passed. Installed-system probes verified full Vim, the
`ipv6.disable=1` boot argument, absent IPv6 addresses and refused IPv6 socket
creation. They also exercised SSH, the guest agent, operator authority,
containers, managed jobs, encrypted Restic backup/restore, local webhook
delivery and menu exit controls. The installed guests had distinct machine
and SSH identities. These installation timings describe the test VMs only.

The first BIOS/UEFI installed-system probes stopped at a stale menu test that
waited for the old numbered prompt. After correcting the probe to use the
visual menu, the installed-system checks were rerun on the same disks and ISO.
The image and runtime packages were unchanged. The earlier failure logs are
retained privately; [ISO evidence](../tests/evidence/iso-alpha9.json) records
this scope. Automated preseeds do not prove interactive installer defaults.

## Source and distribution

- The source suite passed 256 tests with nine environment-dependent skips.
  Shell syntax, Python parsing and publication checks passed. Native guest
  acceptance separately exercised Restic and the terminal menu.
- Credential scanning covered the public Git history and staged source tree.
  The publication guard found no private deployment identifiers or key blocks.
- The corresponding-source collection retains 540 authenticated Debian
  source/version pairs, including an alpha.8 source superset and six new
  security source versions. All 1,651 unique source files, their signatures,
  index hashes and source descriptors were checked again.
- The final split source archive passed checks of 2,511 file hashes and 3,137
  outer headers. Upstream archive bytes and attribution are preserved.
  [Source evidence](../tests/evidence/source-alpha9.json) records the scope.

## Lab upgrade

The existing SSH lab was upgraded through the signed testing channel with a
stopped-VM Proxmox recovery snapshot. The upgrade preserved existing schedules,
queue history and configuration. The requested IPv6 boot setting was applied
explicitly, followed by a reboot and native checks. The sanitized
[lab evidence](../tests/evidence/lab-alpha9.json) records the final results.

## Limits

Secure Boot, arbitrary physical hardware, encrypted/free-space/manual partition
variants, recovery boot and GPU workloads were not tested. Image acceptance
made no real provider requests or external webhook calls. A file-level backup
round trip does not prove application-consistent database recovery.

There is no new cloud image, public hosted APT endpoint, stable-channel promotion,
production support commitment or claim of bit-for-bit reproducible builds.
The release publishes sanitized evidence rather than raw guest logs and identities.
