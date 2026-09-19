# Self-hosted repository and Proxmox helpers

These scripts are optional operator tools. They do not provide a public
Rougarou APT endpoint, DNS name, TLS certificate or production support service.
The installed OS can use its signed local bootstrap repository and direct
Debian security source without a hosted Rougarou channel. Publishing source
or an ISO does not enroll installed machines into a new endpoint.

Choose your own host, unused VM ID, storage and network. Commands below use
explicit placeholders; replace them after reviewing the intended resources.
Never substitute an existing production VM for a disposable test VM.
Hypervisor credentials and private signing keys stay outside the guest image.

## Prepare an installer test VM

`prepare-test-vm.sh` runs from a workstation with authorized Proxmox SSH access.
It requires the matching signed ISO checksum in local `dist/` and the ISO
already uploaded to the selected Proxmox ISO storage. It checks the stored ISO
against the trusted signed checksum, refuses an existing VM ID, creates a new
32 GiB disk and starts the normal interactive installer.

```sh
ROUGAROU_VM_STORAGE=YOUR_VM_DISK_STORAGE \
ROUGAROU_ISO_STORAGE=YOUR_ISO_STORAGE \
ROUGAROU_BRIDGE=YOUR_NETWORK_BRIDGE \
  deployment/prepare-test-vm.sh USER@PROXMOX_HOST NEW_VMID
```

`ROUGAROU_VM_STORAGE` is required. `ROUGAROU_ISO_STORAGE` defaults to Proxmox's
conventional `local` storage name, and `ROUGAROU_BRIDGE` defaults to `vmbr0`.
Confirm those names exist and refer to the intended storage/network, or set
them explicitly. The helper does not move unrelated ISOs, edit existing VMs,
select an installation disk or confirm partitioning. The administrator remains
responsible for resource capacity, VM identity and removing disposable resources.

## Optional repository VM

The repository provisioner runs **on the authorized Proxmox node**. Transfer
`provision-repository-vm.sh`, a reviewed copy of
`repository-cloud-init.yaml`, and your **public** SSH key to that node first.
Never transfer a signing key. Review the cloud-init package/network actions
before starting the VM.

```sh
ROUGAROU_VM_STORAGE=YOUR_VM_DISK_STORAGE \
ROUGAROU_SNIPPET_STORAGE=YOUR_SNIPPET_STORAGE \
ROUGAROU_BRIDGE=YOUR_NETWORK_BRIDGE \
  bash provision-repository-vm.sh NEW_VMID /path/operator.pub /path/repository-cloud-init.yaml
```

The provisioner requires explicit disk storage and snippet-capable storage;
only the bridge has a default (`vmbr0`). It refuses an existing VM, checks
available memory, verifies a pinned Debian cloud-image SHA512, imports it into
a new VM and supplies your public key through cloud-init. The repository VM
uses 2 vCPUs, 2 GiB RAM and a 32 GiB disk. The official dated Debian cloud-image
checksum is served over HTTPS without a detached signature in that directory;
do not describe that upstream image as GPG-verified.

The template uses DHCP and contains no site-specific route, gateway or DNS
configuration. Confirm those settings through your own network. Nginx initially
allows only localhost; configure an explicit client allowlist and firewall
policy before connecting clients. The template does not expose the repository
publicly or configure TLS. Use reviewed HTTPS hosting for an Internet-facing
service, and keep credentials out of URLs, configuration examples and logs.

After cloud-init finishes, the hypervisor's `qm guest cmd NEW_VMID
network-get-interfaces` can report the assigned address. The created service
account is the generic `rougarou` account, authenticated by the supplied key.
Review its authority before using this template beyond a dedicated repository.
Never infer reachability or outbound package access merely from a DHCP lease.

## Sign and publish packages

Prepare verified packages and metadata, then create and test a signed snapshot
using the [repository workflow](../docs/updates.md). The signing identity stays
on your signing host; the server receives only public repository content and
the public verification key. A snapshot must pass review before promotion.
Stable promotion requires the current testing snapshot and explicit evidence.

From the signing/publication workstation:

```sh
deployment/publish-repository.sh \
  /path/to/signed-repository USER@REPOSITORY_HOST \
  rootfs/usr/share/keyrings/rougarou-archive-keyring.gpg testing
```

This verifies the selected local channel, copies immutable history and swaps the
remote channel only after its files arrive. The remote SSH user needs the
script's administrative access to `/srv/rougarou`; review the script before
configuring that authority. It does not generate a key, choose a remote address
or approve a stable release.

SSH host verification is required. Optional `ROUGAROU_SSH_JUMP`,
`ROUGAROU_SSH_KNOWN_HOSTS` and `ROUGAROU_SSH_HOST_KEY_ALIAS` select an authorized
jump host and known-hosts configuration. They are explicit operator settings;
no private topology is provided by the project. Never disable host-key checking.

Validate the actual hosted channel with native APT in a disposable tool
container, supplying your real reviewed URL:

```sh
deployment/verify-hosted-repository.sh \
  https://packages.example.invalid/rougarou/channels/testing/ \
  rootfs/usr/share/keyrings/rougarou-archive-keyring.gpg rougarou-base
```

`packages.example.invalid` is a nonworking documentation placeholder. Replace
it with your endpoint; do not add it to a client. The helper authenticates the
hosted metadata and downloads a selected package. Clients must use `Signed-By`
with the correct public archive key. Do not use `trusted=yes` or disable expiry
and signature checks. HTTP metadata signatures authenticate package contents,
but HTTP does not encrypt client traffic.

## Operations and recovery

Monitor signed metadata expiry and renew reviewed publications before their
`Valid-Until` deadline. Keep old snapshots, packages and by-hash indexes for
clients that cached earlier metadata. Back up the public repository separately
from protected signing-key material and test recovery. A VM snapshot alone is
not an independent backup. Review signing-key expiration, rotation and
revocation alongside repository expiry.

For external VM recovery, [proxmox-checkpoint.py](proxmox-checkpoint.py) captures
an explicitly selected guest's identity and requires stopped-VM snapshot/restore
operations with exact confirmation. See [cloud recovery](../docs/cloud-image.md)
and [guided updates](../docs/guided-updates.md). Never publish checkpoint records:
they contain machine, SSH and network identity. The helper does not attest
application quiescence, create a separate backup or automatically restart a VM.
