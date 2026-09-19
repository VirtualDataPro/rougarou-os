#!/usr/bin/env bash
# Prepare an empty lab VM; the operator performs the normal interactive install.
set -Eeuo pipefail
project=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
# shellcheck source=../versions.env
source "$project/versions.env"
remote=${1:?Usage: prepare-test-vm.sh USER@PROXMOX_HOST NEW_VMID}
vmid=${2:?Missing new VM ID}
vm_storage=${ROUGAROU_VM_STORAGE:?Set ROUGAROU_VM_STORAGE to a VM disk storage pool}
bridge=${ROUGAROU_BRIDGE:-vmbr0}
iso_storage=${ROUGAROU_ISO_STORAGE:-local}
[[ $remote =~ ^[a-z_][a-z0-9_-]*@[A-Za-z0-9.-]+$ ]]
[[ $vmid =~ ^[1-9][0-9]{2,8}$ ]]
[[ $iso_storage =~ ^[A-Za-z][A-Za-z0-9_.-]*$ ]]
[[ $vm_storage =~ ^[A-Za-z][A-Za-z0-9_.-]*$ ]]
[[ $bridge =~ ^[A-Za-z][A-Za-z0-9_.-]*$ ]]
artifact="rougarou-os-${ROUGAROU_VERSION}-${DEBIAN_ARCH}.iso"
[[ $artifact =~ ^[a-zA-Z0-9.-]+\.iso$ ]]
gpgv --keyring "$project/rootfs/usr/share/keyrings/rougarou-archive-keyring.gpg" \
  "$project/dist/$artifact.sha256.asc" "$project/dist/$artifact.sha256"
expected=$(awk -v image="$artifact" '$2 == image {print $1}' "$project/dist/$artifact.sha256")
[[ $expected =~ ^[a-f0-9]{64}$ ]]
ssh -o BatchMode=yes "$remote" bash -s -- "$vmid" "$artifact" "$expected" "$iso_storage" "$vm_storage" "$bridge" <<'REMOTE'
set -Eeuo pipefail
vmid=$1
artifact=$2
expected=$3
iso_storage=$4
vm_storage=$5
bridge=$6
image=$(pvesm path "$iso_storage:iso/$artifact")
if qm status "$vmid" >/dev/null 2>&1; then
  echo "VM $vmid already exists; refusing to overwrite it." >&2
  exit 1
fi
if [ -f "$image.partial" ] && [ ! -e "$image" ]; then
  printf '%s  %s\n' "$expected" "$image.partial" | sha256sum -c -
  mv "$image.partial" "$image"
fi
printf '%s  %s\n' "$expected" "$image" | sha256sum -c -
qm create "$vmid" --name rougarou-test --description \
  'Rougarou OS alpha installer lab; empty disk; operator performs interactive installation' \
  --memory 4096 --cores 2 --cpu x86-64-v2-AES --machine q35 --bios ovmf \
  --scsihw virtio-scsi-single --scsi0 "$vm_storage:32,discard=on,iothread=1" \
  --efidisk0 "$vm_storage:1,efitype=4m,pre-enrolled-keys=0" \
  --ide2 "$iso_storage:iso/$artifact,media=cdrom" \
  --net0 "virtio,bridge=$bridge" --agent enabled=1 --serial0 socket --vga std \
  --boot 'order=ide2;scsi0' --onboot 0 --tags 'rougarou;testing'
qm start "$vmid"
qm status "$vmid"
REMOTE
