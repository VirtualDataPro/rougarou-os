# Build a signed installation ISO

Run on an amd64 Linux host with Docker, Bash and enough space for the ISO and
package cache. Rootless Docker is supported. The builder uses no host disks,
loop mounts, host root mount or privileged container. The project pins its
Debian container digest, netinst version and Codex artifact SHA256 in
`versions.env`. Debian package versions and hashes are recorded per build.

```sh
scripts/check.sh
export ROUGAROU_GNUPGHOME=/path/to/private/gnupg
export ROUGAROU_SIGNING_KEY=YOUR_FULL_SIGNING_FINGERPRINT
# Feed the signing passphrase through stdin from your secret manager.
secret-tool lookup project rougarou-os purpose release-signing | scripts/build-iso.sh
```

The default output directory is `dist/` and download cache is `.cache/`, both
ignored by Git. Choose storage with enough space for both; local paths are
operator configuration, not release metadata.
Never commit the private GPG keyring or the secret manager output.

The builder verifies the Debian CD checksum signature using Debian's packaged
role keyring, verifies the selected ISO's SHA512, downloads packages through
signed Debian APT metadata, and checks the pinned Codex artifact SHA256. It
resolves packages against an empty dpkg status database so dependencies already
present in the build container are included on the ISO. The offline repository
metadata is signed by the Rougarou archive key. The target checks the payload
and uses the corresponding public key for APT.

The remaster retains Debian's BIOS and UEFI boot images using xorriso replay.
It changes the boot menu to text installation, serial installation, expert
installation and rescue. Partitioning and user credentials stay interactive.

The builder also compiles Debian `cdebconf` 0.280 with its
`pkg.cdebconf.nogtk` build profile. A guarded source patch changes only the
native text frontend's palette and header. The initrd retains Debian's kernel
modules and installer components, and adds Rougarou welcome/access questions.
No compiler or frontend build dependencies are added to the installed server.
The ISO carries the pristine authenticated Debian source, exact patch scripts,
copyright notice, rebuilt frontend package and build-tool inventory under
`/rougarou/sources/cdebconf`. The installer manifest records their SHA256 hashes
and both the original and modified initrd hashes.

Outputs include the ISO, SHA256, package manifest, build manifest and boot image
report. Release checksum signatures are produced with the same external signing
identity and distributed with its public fingerprint. Check
`docs/validation.md` before treating an artifact as install-tested.

## Reproducibility limits

This is an auditable versioned build, not a claim of bit-for-bit reproducibility:
Debian APT repositories move, repository signatures contain timestamps, and
ISO timestamps can vary. Preserve downloaded packages, metadata, manifests and
the original ISO to rebuild the same package set. Production reproducibility
requires snapshot-pinned Debian inputs and controlled timestamps.

## Test package bytes before image assembly

`ROUGAROU_PACKAGE_REVISION` in `versions.env` versions the custom agent
packages. `ROUGAROU_BASE_REVISION` independently versions `rougarou-base`,
falling back to the shared revision when absent. Bump the relevant revision
after changing a package already published to testing; never replace an
existing package/version with different bytes. The currently published ISO
keeps its original package revisions even after a testing-channel update.

The builder supports `ROUGAROU_PACKAGES_ONLY=1` to prepare `dist/custom-packages`
with input and artifact hashes, all custom packages, a signed repository of
the complete Debian dependency set, and external source archives.
`scripts/test-package-candidates.sh` installs those packages offline in fresh
Debian using that candidate repository, reinstalls `base-files` to exercise
branding triggers, then tests removal and purge. A full build with
`ROUGAROU_USE_CANDIDATES=1` verifies the input hashes and reuses those exact
tested custom and Debian `.deb` bytes. Supply signing credentials through the same external
keyring/stdin mechanism used above. Normal builds leave these flags unset.

The input fingerprint records file contents,
file and directory modes, and symlink targets without following links. A mode
or link change invalidates candidate reuse even if file contents are identical.
Final artifact and installed-VM checks remain required.

For an independent base-package update, use the already prepared local builder
and a signed baseline repository. This path shares
`image/build-base-package.sh` with the normal ISO builder. It has no network
access, pulls no image, and writes only to the requested empty output directory:

```sh
scripts/build-base-update.sh \
  dist/package-updates/NEW_BASE_VERSION dist/bootstrap-repository
scripts/test-base-update.sh \
  dist/package-updates/NEW_BASE_VERSION dist/bootstrap-repository
```

The base revision must exceed the baseline, and its dependencies must remain
unchanged. The result contains one `.deb`, its `Packages` index, a build manifest,
source fingerprints and their source archive, the builder's package inventory
and checksums. The manifest identifies the
immutable local builder image, verified baseline and new package hash. No
signing key is mounted during assembly; the later signed repository authenticates
the package. Fresh lifecycle and actual upgrade tests run offline in disposable
Debian containers, preserving installed agents, operator files and system policy.

Prepare a new repository source from the verified prior snapshot, replace only
the base package entry, and compare every other package/version/hash unchanged.
Use the [snapshot and testing workflow](updates.md) with a new immutable snapshot
ID and this update's manifest hash as provenance. Preserve the old snapshot and
ISO; do not regenerate an ISO or agent packages merely to publish a base update.
This command does not publish a channel or update an installed machine.

`packages.txt` lists the core Debian package roots. `rougarou-base` depends on
these roots, without depending on optional agent or container packages.
`image/package-profiles.txt` is the shared installer/build mapping: each
non-comment line is `profile-id|space-separated-package-roots`. The builder
validates both fields and downloads the complete union for offline choices;
it copies the profile file to `/rougarou/package-profiles.txt` on the ISO.
The installed set is core plus `rougarou-base` and explicitly chosen profiles.
An empty profile selection adds no agents or container engines. The build
manifest distinguishes core roots and optional profiles from the full set of
packages available in the signed repository. Debian dependencies needed by
external custom packages must also appear in the corresponding profile so
they can be resolved before those custom packages are built.
The `claude` and `opencode` profiles prepare explicit first-login download
flows; their binaries are not redistributed in the ISO or Rougarou repository.
The build manifest records these delivery exceptions separately.

Gemini CLI is packaged as optional `rougarou-gemini`, using its pinned official
JavaScript release bundle and signed Debian Node.js dependency. The builder
preserves the upstream bundle, tagged source, verified locked dependency
archives and notices under `/rougarou/sources/gemini`. No npm installation
scripts, browser installation, account authentication or model calls run
during packaging. Version pins and reviewed notice overrides are build inputs.

Herdr is distributed as its own `rougarou-herdr` package. Its official stable
binary and source archive have separate reviewed SHA256 pins in `versions.env`.
The builder retains the exact tagged Apache-2.0 license, vendored notices, and
license declarations and notices for every registry dependency in the tag's
`Cargo.lock`; each registry archive is verified against the lockfile checksum.
For crates that omit notice files, `image/herdr-license-overrides.json` pins
texts from their upstream commits (or the matching companion crate's commit).
The two r-efi versions declare licensing in README and AUTHORS rather than a
separate license file; those original texts are retained. Those source
archives are included on the ISO under `/rougarou/sources/herdr`, and the build
manifest records their hashes. No upstream installer is executed and no Herdr
service is enabled by packaging. Starship and the system tool additions come
from signed Debian packages with recommends disabled.

## Development signing identity

The initial public fingerprint is:

```text
0D81 290A FF96 B964 20D5 75FD 8642 1693 79DA F9AD
```

It is an Ed25519 development archive key expiring 2027-09-18. Only its public
export belongs in source or release artifacts. Keep private material, passphrase
and revocation certificate outside the repository and web root; back them up
through a separate protected process. Operators building with their own key
must distribute and verify its public fingerprint independently. Key rotation,
revocation and source-retention obligations remain release responsibilities.
