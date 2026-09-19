# Downloads and verification

Download Rougarou OS from the
[alpha.8 release](https://github.com/VirtualDataPro/rougarou-os/releases/tag/v0.1.0-alpha.8).
Choose one image:

| File | Purpose |
| --- | --- |
| `rougarou-os-0.1.0-alpha.8-amd64.iso` | Interactive installation onto a new VM disk |
| `rougarou-os-0.1.0-alpha.8-cloud-amd64.qcow2` | Standalone cloud-init disk; configure your account and SSH key before boot |

The cloud image has **no default login or password**. See
[cloud onboarding](cloud-image.md#provision-an-operator-explicitly).

## Check the release signature

Download `release-SHA256SUMS`, `release-SHA256SUMS.asc` and
`rougarou-archive-keyring.gpg` alongside the assets you selected. Use a new
directory so older files cannot be mistaken for this release. Confirm the
signing fingerprint through a trusted copy of the project documentation:

```text
0D81 290A FF96 B964 20D5 75FD 8642 1693 79DA F9AD
```

```sh
gpg --show-keys --with-fingerprint ./rougarou-archive-keyring.gpg
gpgv --keyring ./rougarou-archive-keyring.gpg \
  release-SHA256SUMS.asc release-SHA256SUMS
```

Stop if the fingerprint differs or signature verification fails. After a good
signature, check the downloaded assets:

```sh
sha256sum --check --ignore-missing release-SHA256SUMS
```

Every file you intend to use must appear with `OK`. Missing files are skipped
because the signed list also covers the other image and source materials.
The key identifies Rougarou's release signer; it does not establish that the
software is free of defects. The current development key expires on 2027-09-18.

The ISO also has its own signed `.iso.sha256` list. The cloud metadata archive
contains the cloud build manifest, installed-package inventory, sealed-image
audit and authenticated Debian APT evidence. Follow the
[cloud verification steps](cloud-image.md#verify-the-cloud-download) before
extracting that archive.

## Source materials

The release provides corresponding source alongside its binary images:

- `rougarou-os-0.1.0-alpha.8-corresponding-source.tar.part-01`
- `rougarou-os-0.1.0-alpha.8-corresponding-source.tar.part-02`
- `rougarou-os-0.1.0-alpha.8-corresponding-source.manifest.json`
- `rougarou-os-0.1.0-alpha.8-corresponding-source.README.md`

These are parts of one tar archive. Verify both parts, the manifest and README
against the signed release list before following the README's reassembly and
whole-archive verification instructions. Extract into a new directory.

The bundle retains exact Debian source packages and signed index evidence,
the bundled agent/workspace sources and dependency notices, installer patch
materials, and collection/verification tools. Upstream archives retain their
original bytes and attribution. The outer archive uses generic ownership and
contains no workstation paths.

Rougarou's integration source, patches and build scripts are in the
[matching Git tag](https://github.com/VirtualDataPro/rougarou-os/tree/v0.1.0-alpha.8).
The signed release list also covers `rougarou-os-0.1.0-alpha.8-source.tar.gz`,
an archive of that exact source tree.
See [NOTICE](../NOTICE) and the packages' own copyright files for licenses.
The release manifests identify the shipped versions; the source bundle does
not make a claim of bit-for-bit reproducible builds.
