# Release validation

Release: **0.1.0-alpha.8**. The signed release manifest and checksum list identify
its exact images and source tree. Results below describe the checks performed;
they do not establish support for every Debian or Proxmox configuration.

## Installer ISO

The released ISO has SHA256
`e6f014401a422970e843a0b02f335766b11b401ad340d1ed335c0f2995036ec3`.
Its signed media and payload audits passed, all 99 recorded source inputs
matched, and the exact final image booted successfully under BIOS and UEFI.
Native package install/remove/purge tests passed, as did upgrades preserving
both unmodified and operator-modified configuration.

Three fresh offline installations and their installed-system checks passed on
a candidate with the same runtime contents:

| Firmware | Agent | Docker | Podman / Herdr | Installation time |
| --- | --- | --- | --- | --- |
| BIOS | None | Rootless | Neither | 467 seconds |
| UEFI | Codex | Rootless | Both | 501 seconds |
| BIOS | Gemini CLI | System | Neither | 416 seconds |

The nine-check matrix also covered original BIOS/UEFI boots and distinct
installed machine and SSH identities. Installed checks exercised SSH, the guest
agent, containers, operator tools and a real Restic backup/restore round trip.
These timings are observations from disposable test VMs, not a performance
promise for other storage or hardware. Automated profile selections do not
prove the highlighted defaults in the interactive installer.

The final ISO was rebuilt to record the cloud-sealing source fix. A comparison
of all 2,305 ISO entries proved that paths, types, modes and runtime bytes were
unchanged, including the installer initrd, all 277 repository packages, package
indexes, boot configuration and public key payload. Seven metadata/provenance
entries changed: build records, generated frontend logs, checksums and renewed
repository signatures. The nine-check result therefore carries forward through
verified runtime equivalence; **the three full installations were not rerun on
the final metadata-only rebuild**. Fresh boot and signed-media checks were run
on the exact final image. [Detailed ISO evidence](../tests/evidence/iso-alpha8.json)
records both hashes and the comparison scope.

## Cloud image

The released QCOW2 has SHA256
`54b4bb61b4a49b5e6a5440938561c27c1be84eeba5d29bea70daa5dfca3dae6c`.
All 12 recorded project/cloud inputs matched the source tree. A powered-off
audit found no operator accounts, SSH host keys, initialized machine ID,
root SSH directory, random seed or cloud instance cache.

A scan of the full decoded 8 GiB disk, including unused space, found no
complete OpenSSH keys or unattributed complete private-key structures.
Eight embedded cryptographic self-test keys were matched byte-for-byte to
the authenticated GnuTLS library and its retained upstream source. Token-shaped
constants and generic network examples were likewise attributed to upstream
package content; operator-specific identifier checks found no matches. The
build zeroes free space using the unmounted ext4 allocation bitmap before
compression. An independent allocation-map audit checked all 1,492,220 free
root-filesystem blocks and found every byte zero; see
[free-block evidence](../tests/evidence/cloud-free-blocks-alpha8.json).
[Privacy evidence](../tests/evidence/privacy-cloud-alpha8.json) and a
[native sealing regression](../tests/evidence/cloud-zeroing-regression.json)
describe this scope without publishing development identifiers.

Two fresh clones passed BIOS/UEFI boot, supplied-key SSH login, filesystem
growth to 12/16 GiB virtual disks, explicit headless/unprivileged account
policies, updater source-policy checks, and an actual rootless Docker payload.
Their machine and SSH identities were distinct. Both shut down cleanly; the
sealed release image was never booted or modified by these tests. See
[clone evidence](../tests/evidence/cloud-alpha8.json) and
[build evidence](../tests/evidence/cloud-build-alpha8.json).

## Source and distribution checks

- The local source suite ran 235 tests with five environment-dependent skips;
  shell syntax, Python parsing, repository invariants and publication checks
  passed. Native guest tests separately exercised Restic operations.
- Credential scanning covers the complete public Git history. The publication
  guard checks the source tree for private deployment addresses, personal home
  paths, private-key headers and nonsynthetic hardware addresses.
- The corresponding-source bundle retains 534 Debian source/version pairs,
  authenticated index evidence, bundled agent/workspace sources and dependency
  notices. All 1,629 unique Debian upstream files passed independent hashing;
  source descriptors agree with their authenticated index records.
- All 2,463 bundle file hashes and 3,073 outer archive headers were checked.
  Outer ownership, paths and timestamps are normalized; authenticated upstream
  archives keep their original bytes and legitimate author attribution.
- The ISO and cloud manifests record component versions and input hashes.
  [Cloud source coverage](../tests/evidence/cloud-source-binding-alpha8.json)
  maps all 389 installed packages to the signed repository and retained source.

The release publishes sanitized test summaries rather than raw VM logs,
screenshots, host identifiers or development history. Gitleaks and byte-pattern
checks are useful evidence, not a guarantee that arbitrary data is harmless.

## Limits

Secure Boot, TPM/network disk unlock, encrypted/free-space/manual partition
variants, arbitrary physical hardware or GPU workloads, and real provider
account authentication/model requests were not tested. A GRUB recovery entry
was checked for presence; successful recovery boot is not claimed. A file-level
Restic round trip does not establish application-consistent database recovery.
The release does not claim bit-for-bit reproducible builds or independent
security certification.

There is no public APT endpoint or stable promotion implied by publishing this
alpha. A self-hosted channel needs separate authentication, retention, expiry
monitoring and recorded testing approval. Debian security updates remain directly
available. This alpha has no production support commitment.
