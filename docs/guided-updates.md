# Guided updates and recovery

Rougarou provides an explicit update workflow over the sources already
configured on the host. It does not change repository enrollment or enable
automatic package installation.

```sh
rougarou update refresh
rougarou update plan
rougarou update plan --json
# Create/check a recovery point in Proxmox or your backup system, then:
rougarou update apply --plan PLAN_ID --recovery-point 'HYPERVISOR VM_ID snapshot SNAPSHOT_NAME' --confirm
rougarou update status
rougarou update recovery
```

`refresh` explicitly downloads metadata. `plan` only reads cached metadata and
installed package state; it does not contact repositories, install packages or
persist a plan. Review its exact names, old/new versions, source channels,
SHA256 digests, download size and reboot hints. Its ID binds those choices to
this machine, installed inventory, automatic-package marks, configured sources
and signing keys. A plan expires after 24 hours from refresh or the earliest
signed repository expiry, whichever comes first. Any relevant change requires
a new plan. A channel publishing new bytes requires an explicit refresh.

`refresh` and `apply` request administrator access through the fixed packaged
helper. In a terminal this uses normal sudo authentication; headless calls use
noninteractive sudo and fail if authorization is unavailable. Running the
whole operator CLI as root is unnecessary. Nothing installs at login or on a
timer, promotes a channel, or automatically reboots.

## Sources and verification

The guided updater accepts configured Rougarou `testing`/`stable` channels, the
signed local ISO baseline at `/var/cache/rougarou/repo`, and direct Debian
`trixie-security` at `https://security.debian.org/debian-security`. It never
adds a source or changes your enrollment. Ordinary Debian and vendor sources,
trust/expiry bypasses, custom keys, credentials in URLs, foreign architectures
and custom APT preferences require administrator review and are rejected.
Disabled source entries remain disabled. This initial version supports amd64.

Refresh uses an isolated APT metadata generation. Native APT authenticates each
source; Debian's `sqv` independently verifies its InRelease against that source's
fixed keyring. The updater checks signed Release identity, Date/Valid-Until and
SHA256 hashes of the exact package indexes. Apply downloads only the reviewed
versions, verifies the .deb SHA256 and size from those signed indexes, then uses
an empty source configuration and the verified local archives for installation.
This follows APT's archive-signature → index → package hash chain. Archive
signatures authenticate the publisher; they do not establish that the
publisher's code is harmless. [Debian apt-secure](https://manpages.debian.org/trixie/apt/apt-secure.8.en.html)

The routine uses `upgrade --with-new-pkgs` planning. Existing packages can be
updated and new dependencies added; removals and downgrades are refused. Held
packages stay held. Existing automatic/manual marks are preserved and new
upgrade dependencies remain automatic. Incompatible updates stay back for
manual review. Root-owned isolated APT configuration excludes ambient hooks,
authentication overrides and repository changes. Package maintainer scripts
still run normally and may restart affected services; schedule maintenance
accordingly. Existing conffiles use `--force-confold`.
[Debian apt-get](https://manpages.debian.org/trixie/apt/apt-get.8.en.html)

## Recovery is a real restore point

`--recovery-point` records your explicit attestation that a concrete VM snapshot
or backup exists and is suitable for this update. The guest does **not** contact
the hypervisor or independently prove the reference. No Proxmox credentials are
stored in the image. A string that merely looks like a snapshot name is not a
backup. Check the VM identity, disks, snapshot time and application consistency
on the host before confirming an update.

The tool records private before/after inventories, reviewed plan, recovery
reference, logs and result under `/var/lib/rougarou/updates/transactions/ID/`.
`rougarou update status` shows a sanitized last-transaction result. A fixed
pre-unpack helper rechecks machine, inventory, automatic marks, expiry and source
policy while native APT owns the package lock. An interrupted `started`, failed
or incomplete dpkg transaction needs inspection before retrying. Use the
hypervisor console if SSH or networking is affected.

A VM snapshot restore discards subsequent guest changes. It may not cover
external disks, databases or remote effects; protect and quiesce those
separately. Downgrading packages does not reverse application data migrations.
Rougarou does not claim atomic APT rollback and never performs an automatic VM
restore. `rougarou update recovery` gives inspection and restore guidance.

## Notifications API

`updates.repository_status()` uses configured local cached InRelease files
from native APT or the guided cache. It performs no network requests and no
persistent cache writes; cleartext verification uses a private temporary file
which is removed afterward. A three-second overall subprocess budget bounds ordinary checks; `unavailable` means the local check timed out. The result contains source filename, channel,
status (`valid`, `expiring`, `expired`, `missing`, `invalid`, `unavailable`, `policy_error`),
expiry and check time. It contains no source URLs or credentials.
`updates.reboot_status()` reads the current boot ID, standard marker and sanitized
last transaction: `required` reflects the system marker; `recommended` also
covers completed core-package updates in this same boot. Its fixed reason enum
distinguishes those cases, and a new boot clears the transaction recommendation. `expiring`
means seven days or less remain. Missing cached metadata means refresh is
needed, not that a remote server is down.

## Validation procedure

The source tests cover source policy, expiry/channel identity, parser/removal
guards, confirmation and recovery references, stale plans, fixed sudo dispatch
and sanitized status errors. The native Debian fixture uses real signed
packages and APT to upgrade a package and add a dependency while checking
unrelated inventory, holds, automatic marks and transaction records. It also
rejects changed inventory, bypass sources, altered metadata, unrelated signing
keys and modified package bytes before installation.

A fixture recovery reference is not evidence of a VM snapshot. Separately test
an actual checkpoint and restore in an explicitly allocated disposable VM,
including a marker rollback and stable machine/SSH/network identity. Record
that scope without publishing raw checkpoint records or credentials. See
[public release validation](validation.md) for the current artifact's status.
