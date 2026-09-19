# Rougarou cloud image

The cloud artifact is a separate, standalone QCOW2 for Proxmox and QEMU. It uses Debian's standard generic amd64 kernel, cloud-init's NoCloud datasource, Rougarou's signed package snapshot, and the same operator tools as the ISO. It contains rootless Docker prerequisites; the per-user daemon starts only when the operator chooses `rougarou-docker setup`. No AI agent, provider credential, notification credential, operator account, or persistent worker is baked in.

Obtain the standalone alpha.8 QCOW2 and signed metadata from the matching
[project release](https://github.com/VirtualDataPro/rougarou-os/releases/tag/v0.1.0-alpha.8).
See [validation](validation.md) for tested configurations. The separate installer
ISO provides interactive storage, account, access and software choices;
see [first installation](first-test.md).

## Verify the cloud download

Download the QCOW2, `rougarou-os-0.1.0-alpha.8-cloud-metadata.tar.xz`, its `.asc` signature, and `rougarou-archive-keyring.gpg` from that release into a new, otherwise empty directory. Run these commands inside it. Confirm the archive key against the trusted project fingerprint `0D81290AFF96B96420D575FD8642169379DAF9AD`; stop if any command fails.

```sh
EXPECTED=0D81290AFF96B96420D575FD8642169379DAF9AD
ACTUAL=$(gpg --show-keys --with-colons ./rougarou-archive-keyring.gpg |
  awk -F: '$1 == "fpr" { print $10; exit }')
META=rougarou-os-0.1.0-alpha.8-cloud-metadata.tar.xz
test "$ACTUAL" = "$EXPECTED" &&
  gpgv --keyring ./rougarou-archive-keyring.gpg "$META.asc" "$META" &&
  tar -xJf "$META" --no-same-owner --no-same-permissions &&
  gpgv --keyring ./rougarou-archive-keyring.gpg SHA256SUMS.asc SHA256SUMS &&
  sha256sum --check SHA256SUMS
```

The first signature check authenticates the metadata archive before extraction. The signed checksum list then verifies the QCOW2 and every retained metadata file together. Import the image only after every listed file reports `OK`.

The reviewed upstream is Debian's dated `debian-13-generic-amd64-20260914-2601.qcow2`. Its SHA512 is pinned in [the upstream record](../image/cloud/upstream.json). Debian serves this directory's checksum over HTTPS and does **not** publish a detached GPG signature there. Rougarou records that limitation, retains the dated metadata, checks the exact image digest, authenticates added Debian packages through signed APT metadata, and signs its own final artifact. The image is not described as upstream-GPG-verified. See [Debian's official image directory](https://cloud.debian.org/images/cloud/trixie/20260914-2601/) and [cloud image guidance](https://wiki.debian.org/Cloud).

Cloud-specific package roots live in [image/cloud/packages.txt](../image/cloud/packages.txt). They do not add cloud-init to the ISO's core profile. The cloud repository includes the inherited cloud-specific package versions and their dependencies so subsequent updates remain available through the Rougarou promotion process. Only Debian security retains a direct upstream runtime source. Automatic package upgrades and automatic reboot remain disabled.

## Build and verify

Use a separately signed immutable cloud repository and an empty output/work directory. The builder never edits an existing artifact. It boots a new overlay of the pristine image with no network, installs from the local authenticated repository, configures the image, clears machine identity/SSH host keys/cloud-init build state, and shuts down. It then flattens a new working disk, zeroes free space on the root and EFI filesystems offline, and compresses the final standalone QCOW2. The upstream image remains unchanged. Release review also scans the decoded disk for recoverable credentials and build identity.

Check out the release's exact source revision. The accepted base package must be in its original candidate directory beside `package-inputs.json`; a package built from different runtime inputs is rejected. Keep the dated upstream QCOW2 beside its original `SHA512SUMS` and Debian JSON metadata. That cache directory also needs `apt-lists/` containing the authenticated Debian index files named by `CLOUD_PROVENANCE_JSON`; the released `debian-apt-provenance.tar.xz` retains those files. Verify the release bundle's signature before extracting that evidence.

For a new reviewed cloud package set, `scripts/prepare-cloud-source.py SIGNED_BASELINE CACHE EMPTY_SOURCE --project PROJECT` independently checks the saved Debian signatures, index hashes and every added package before combining it with the baseline. Its input cache contains `apt-debs/`, a `Packages` index for those downloads, the signed `apt-lists/`, and the dated Debian image metadata. Sign the resulting source through the normal repository workflow before using it to build an image. The hosted testing snapshot uses short validity; the immutable in-image bootstrap uses deliberately long validity.

```sh
scripts/build-cloud-image.sh VERIFIED_DEBIAN_QCOW2 SIGNED_CLOUD_REPOSITORY ACCEPTED_BASE_DEB CLOUD_PROVENANCE_JSON EMPTY_OUTPUT EMPTY_WORK
scripts/test-cloud-image.sh OUTPUT/rougarou-os-VERSION-cloud-amd64.qcow2 OUTPUT/build-manifest.json EMPTY_EVIDENCE_DIRECTORY
```

Prepare the tool container with `docker build -t rougarou-vm-test:trixie -f containers/vm-test.Dockerfile containers`, followed by `docker build -t rougarou-cloud-builder:trixie -f image/cloud/Dockerfile image/cloud`; use the pinned `DEBIAN_CONTAINER` build argument from `versions.env` for the first command.

The cached `rougarou-cloud-builder:trixie` tool container is resolved to its immutable local image ID for each build. Build and guest APT operations are offline. The manifest records upstream trust, exact repository metadata hashes, installed package versions, build inputs, image hash, and the tool image ID. Final release signing and testing-channel publication follow the normal reviewed release process.

Two disposable test clones use distinct NoCloud instance IDs and SSH keys. BIOS and UEFI tests cover successful cloud-init, independent machine IDs and SSH host keys, root filesystem growth, QEMU guest agent, real key-only SSH, explicit sudo versus unprivileged authority, masked rootful container services, and absence of agents and persistent worker services. The input image's hash must remain unchanged.

## Provision an operator explicitly

**There is no default username or password.** Before first boot, set a username
and your public SSH key in Proxmox's Cloud-Init panel, configure networking, and
turn **Upgrade packages** off. Then connect with `ssh your-operator@VM_ADDRESS`
using the matching private key on your workstation. The private key stays on
your workstation. A cloud image booted without account configuration has no
usable login; shut it down, configure its cloud-init drive, and boot it again.

Attach a NoCloud seed drive labelled `CIDATA`, containing `meta-data` and `user-data`. Every clone needs a unique `instance-id`. The image deliberately contains no usable login without a seed. Native cloud-init configuration controls the operator's authority; this example explicitly grants passwordless sudo:

```yaml
#cloud-config
users:
  - name: your-operator
    shell: /bin/bash
    lock_passwd: true
    groups: [users]
    sudo: ['ALL=(ALL) NOPASSWD:ALL']
    ssh_authorized_keys:
      - ssh-ed25519 REPLACE_WITH_YOUR_PUBLIC_KEY
ssh_pwauth: false
disable_root: true
package_update: false
package_upgrade: false
```

For an unprivileged account use `sudo: null`; arrange a separate administrator. Never publish a seed containing credentials. Proxmox's native `--ciuser` and `--sshkeys` configuration creates the chosen account and inherits Debian's passwordless sudo policy. Use custom user-data with `sudo: null` when that authority is not desired. Set `qm set VMID --ciupgrade 0`; in the Proxmox Cloud-Init panel, turn **Upgrade packages** off before first boot. Proxmox otherwise emits `package_upgrade: true`, overriding the image's manual-update default. Its cloud-init drive supplies the chosen key and DHCP/static network settings. See [NoCloud documentation](https://docs.cloud-init.io/en/latest/reference/datasources/nocloud.html). Password SSH and root SSH are disabled by the image. Rootful Docker remains masked.

## Import into Proxmox

Run these commands as an authorized administrator on the Proxmox node. Copy the QCOW2 and your **public** SSH key to that node first, and verify the image against Rougarou's signed checksum bundle. Choose a new VM ID, a storage pool supporting VM disks, and the intended network bridge. The commands refuse an existing VM and let Proxmox generate a new MAC address.

```sh
(
set -eu
VMID=YOUR_UNUSED_NUMERIC_VM_ID
STORAGE=YOUR_VM_DISK_STORAGE
BRIDGE=YOUR_NETWORK_BRIDGE
IMAGE=/path/rougarou-os-0.1.0-alpha.8-cloud-amd64.qcow2
PUBLIC_KEY=/root/operator.pub

if qm config "$VMID" >/dev/null 2>&1; then
  echo 'VM already exists; choose a new ID.' >&2
  exit 1
fi
test -f "$IMAGE"
test -f "$PUBLIC_KEY"
ssh-keygen -lf "$PUBLIC_KEY"
qm create "$VMID" --name rougarou-cloud --memory 2048 --cores 2 \
  --cpu x86-64-v2-AES --machine q35 --bios ovmf --ostype l26 \
  --scsihw virtio-scsi-single \
  --efidisk0 "$STORAGE:1,efitype=4m,pre-enrolled-keys=0" \
  --net0 "virtio,bridge=$BRIDGE" --agent enabled=1 \
  --serial0 socket --vga std --onboot 0
qm importdisk "$VMID" "$IMAGE" "$STORAGE" --format raw
# Read the volume ID that Proxmox just imported; do not guess its disk number.
DISK=$(qm config "$VMID" | sed -n 's/^unused0: //p')
test -n "$DISK"
qm set "$VMID" --scsi0 "$DISK,discard=on,iothread=1" \
  --ide2 "$STORAGE:cloudinit" --boot order=scsi0 \
  --ciuser your-operator --sshkeys "$PUBLIC_KEY" \
  --ipconfig0 ip=dhcp --ciupgrade 0
qm resize "$VMID" scsi0 32G
qm cloudinit update "$VMID"
qm start "$VMID"
# After boot, find the address and connect as the chosen operator using its key.
qm guest cmd "$VMID" network-get-interfaces
)
```

Cloud-init expands the root filesystem on first boot. For a static address, replace `ip=dhcp` with the network's explicit `ip=ADDRESS/PREFIX,gw=GATEWAY` configuration before starting the new VM. In the UI, configure the Cloud-Init user, public key and network, and leave **Upgrade packages** disabled. Native Proxmox account creation grants passwordless sudo; custom NoCloud user-data can instead select restricted authority as described above. The disposable test provisioner is separate from this general installation route.

## Proxmox recovery checkpoint

[deployment/proxmox-checkpoint.py](../deployment/proxmox-checkpoint.py) is an external Proxmox-host helper. Hypervisor authentication stays on the operator's workstation/Proxmox host, never in the guest OS. The generic helper accepts an explicitly identified operator VM. The separate disposable provisioning workflow refuses existing VMs; use only explicitly allocated test resources.

Checkpoint capture supports key-based cloud-init configuration and refuses a VM with a configured `cipassword`; use the operator’s native Proxmox recovery procedure for that configuration. It never exports cloud-init passwords or authorized-key contents.

Run `capture` while the chosen guest is running, explicitly naming its VMID, expected name, MAC, and IP. Shut down that guest through the operator's normal procedure. `create` requires a stopped guest and exact `VMID:snapshot` confirmation. It records the VM's disks, network configuration, SMBIOS identity, guest machine ID, SSH host-key fingerprint, snapshot creation time, and a unique checkpoint token. Replacing a snapshot under the same name invalidates the record.

```sh
python3 proxmox-checkpoint.py capture NEW_VMID --expected-name NAME --expected-mac MAC --expected-ip IP --record /root/checkpoint.json
# Operator explicitly shuts down this VM.
python3 proxmox-checkpoint.py create NEW_VMID --record /root/checkpoint.json --snapshot before-update --confirm NEW_VMID:before-update
# Operator starts the VM and performs the update.
# If restore is chosen, operator explicitly shuts it down first.
python3 proxmox-checkpoint.py restore NEW_VMID --record /root/checkpoint.json --snapshot before-update --confirm NEW_VMID:before-update
# Operator explicitly starts it again, then checks its identity.
python3 proxmox-checkpoint.py verify NEW_VMID --record /root/checkpoint.json
```

Restore never auto-starts or reconfigures the VM. The helper proves a stopped-VM disk snapshot; it does not itself attest graceful application shutdown or replace application-consistent database backups. The guest updater's `--recovery-point` remains an operator-attested reference, even when an external host helper created the snapshot. Validate actual marker rollback and stable machine/SSH/network identity in a disposable Proxmox VM before relying on the recovery workflow.
