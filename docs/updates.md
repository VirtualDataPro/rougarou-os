# Signed updates and promotion

Rougarou's policy is **direct Debian security updates; all other packaged
updates pass through Rougarou testing and explicit promotion**. The initial
repository is a curated package set derived from the verified ISO dependency
closure. It is not a complete Debian mirror or a claim of production support.
These tools create a local publishable tree. No public endpoint or trusted TLS
domain is provisioned; use a reviewed host you operate or trust. See
[self-hosted repository deployment](../deployment/README.md).

An installed machine should use the Rougarou stable channel plus Debian's
`trixie-security` suite. It must not also enable ordinary Debian `trixie`,
`trixie-updates`, backports or testing, which would bypass this gate. Keep the
Debian archive keyring separate from the Rougarou archive keyring. At equal APT
priority the newer version wins; a higher pin for Rougarou can inadvertently
suppress a Debian security fix. Monitor held upgrades: a security package may
need a dependency not yet available in the curated snapshot.
[APT priority rules](https://manpages.debian.org/trixie/apt/apt_preferences.5.en.html)

The operator chooses when to run updates. In the tested installation,
unattended-upgrades is installed and its Debian security origin rules are
available, but periodic index refresh and unattended installation are disabled:
there is no `20auto-upgrades`/`10periodic` configuration or `APT::Periodic`
override, and the relevant intervals default to zero. Enabled APT systemd
timers alone do not enable automatic installation. Automatic reboot is explicitly
disabled. Verify the effective policy with `apt-config dump` on your installation.

To opt into automatic eligible Debian security updates, run
`sudo dpkg-reconfigure -plow unattended-upgrades`, enable updates, and inspect
`apt-config dump`. Keep ordinary Debian suites disabled and retain the allowed
Debian security origins; the Rougarou repository is not selected by those rules.
Ordinary Rougarou upgrades still require promotion and operator initiation.
The optional OpenCode and Claude download helpers carry reviewed version/hash
pins in signed Rougarou packaging and disable native updates through their
managed launchers. They install only after explicit operator confirmation; see
[deferred agents](deferred-agents.md). Other operator-installed agents need their
own version/checksum review; signing an APT repository does not govern arbitrary
upstream self-updaters.

## Trust and metadata

The tool generates `Packages`, deterministic `Packages.gz`, `Release`,
`InRelease` and `Release.gpg`. Signed release metadata authenticates SHA-256
hashes of the indexes; indexes authenticate every included `.deb` by size and
SHA-256. The importer validates those hashes before signing but does not
independently re-download Debian's upstream signed metadata. Its source must
come from the builder's authenticated APT download process, with that build's
verification record supplied as provenance.

Use the exported public key at
`/usr/share/keyrings/rougarou-archive-keyring.gpg` with `Signed-By`. Never use
`trusted=yes`, disable signature checks, or distribute the private signing key.
[APT repository authentication](https://manpages.debian.org/trixie/apt/apt-secure.8.en.html)

`Origin` and `Label` are `Rougarou`; `Codename` stays `rougarou`. The channel is
the `Suite` (`testing` or `stable`). Remote metadata expires after 30 days by
default. Publish a freshly signed generation of the same tested snapshot before
expiration; stable renewal still requires evidence and explicit approval.
An immutable ISO's bootstrap repository can use an explicitly longer validity
window (up to 3650 days). This permits later offline installation, but does not
make old packages current or eliminate key-compromise risk.
It does not override signing-key expiration: the development key expires on
2027-09-18, so the longer metadata window is not a ten-year installation promise.

## Local workflow

Requirements are Python 3.11+, GnuPG (`gpg` and `gpgv`), a POSIX filesystem and
an external signing key. The tool does not generate keys. Use a full signing
fingerprint; keep its `GNUPGHOME` outside the source and generated repository,
with mode `0700`. Commands below use placeholders, not a hosted service.

Create an immutable snapshot from a verified flat repository containing an
existing `Packages` index and its `.deb` files:

```sh
python3 scripts/repo.py snapshot \
  --root /srv/rougarou-repository \
  --source /path/to/verified-iso-package-repository \
  --snapshot 20260918T120000Z \
  --provenance 'ISO build identifier and verification record' \
  --gnupghome /private/rougarou-gnupg --key FULL_SIGNING_FINGERPRINT
```

Publish to testing, then install/upgrade disposable VMs against that channel:

```sh
python3 scripts/repo.py promote \
  --root /srv/rougarou-repository --snapshot 20260918T120000Z \
  --channel testing \
  --keyring rootfs/usr/share/keyrings/rougarou-archive-keyring.gpg \
  --gnupghome /private/rougarou-gnupg --key FULL_SIGNING_FINGERPRINT
```

Record actual results using
[`promotion-evidence.example.json`](../repository/promotion-evidence.example.json)
as the schema. Replace every example; a passing evidence file must name this
snapshot, list performed tests, identify the approving operator and set
`result` to `pass`. Evidence should contain public artifact identifiers and
results, never credentials or raw sensitive logs. The command validates the
evidence structure; it cannot prove that an operator performed those tests.

```sh
python3 scripts/repo.py promote \
  --root /srv/rougarou-repository --snapshot 20260918T120000Z \
  --channel stable --approve-stable --evidence /path/to/actual-results.json \
  --keyring rootfs/usr/share/keyrings/rougarou-archive-keyring.gpg \
  --gnupghome /private/rougarou-gnupg --key FULL_SIGNING_FINGERPRINT
```

Stable promotion requires the **current signed testing snapshot**, valid
signatures and payload hashes, an evidence file and `--approve-stable`. No
command automatically promotes a new Debian download to stable. Publication
includes signed promotion metadata and a separate audit record. Existing
snapshots and publications are retained; reusing a snapshot ID is refused.

Verify a generated repository independently:

```sh
python3 scripts/repo.py verify \
  --repo /srv/rougarou-repository/channels/stable \
  --keyring rootfs/usr/share/keyrings/rougarou-archive-keyring.gpg
```

The ISO builder's lower-level operation signs an already indexed flat directory:

```sh
python3 scripts/repo.py sign-flat --repo /path/to/iso/rougarou/repo \
  --suite stable --valid-days 3650 \
  --gnupghome /private/rougarou-gnupg --key FULL_SIGNING_FINGERPRINT
```

This low-level signing primitive is for image assembly, not a substitute for
the testing-to-stable release process. Protect signing-key access accordingly.

For automation, `ROUGAROU_SIGNING_PASSPHRASE_FD` can name an already-open file
descriptor containing one newline-terminated passphrase. The tool reads it
once into memory and supplies it to GPG over stdin; it does not put the phrase
in arguments, logs or temporary files. Without that variable, GPG uses its
normal agent/pinentry behavior. Retrieve the phrase from the operator's secret
store rather than placing it in an environment variable or source file.

## Hosting contract and limits

The generated layout contains `snapshots/`, `publications/`, `channels/`,
`pool/`, `index-by-hash/` and `audit/`. Channel names are local symlinks switched
atomically after a complete generation has been signed. Each generation
retains hardlinks to historical packages and by-hash indexes, so a client that
fetched the previous index can still fetch its files after a channel changes.
The tool serializes local snapshot/promotion operations with a file lock.

A static HTTPS server must follow those channel symlinks, serve the retained
files, and publish metadata with short/no cache lifetime. Package and by-hash
objects may be cached immutably. Serve only generated public repository
content; keep private keys and source verification logs outside the web root.
Do not garbage-collect retained generations until a separate retention policy
accounts for offline and stale clients.

An object store or hosting provider that does not preserve symlinks needs a
publication adapter. Upload all immutable packages and by-hash objects first,
then publish channel metadata; test updates during publication. These scripts
do not provision a server, DNS, TLS, authentication or an object-store adapter.
The separate [deployment scripts](../deployment/README.md) can provision a new
operator-selected Proxmox VM and publish to its static server. Its initial
access policy is localhost-only; explicitly configure client access. Public
DNS, TLS, authentication and object-store hosting need separate setup.

After an HTTPS endpoint is chosen, a flat APT source looks like:

```text
Types: deb
URIs: https://packages.example.invalid/rougarou/channels/stable/
Suites: ./
Signed-By: /usr/share/keyrings/rougarou-archive-keyring.gpg
```

Do not configure this placeholder on installed machines. A flat repository uses
an exact-path suite and no `Components` field.
[APT source format](https://manpages.debian.org/trixie/apt/sources.list.5.en.html)

Before production use, establish signing-key backup and rotation, emergency
revocation, repository monitoring and renewal, a dependency-completeness check,
upgrade/rollback evidence, maintenance ownership and tested HTTPS hosting.

## Guided operator commands

The operator CLI provides `rougarou update refresh`, `plan`, `apply`, `status` and `recovery`.
The [guided update guide](guided-updates.md) explains exact plan IDs, signed
metadata verification and explicit recovery references. Repository promotion
remains a separate maintainer action; a guest update never promotes testing to
stable. `rougarou menu` exposes the same commands in a terminal menu.
