#!/usr/bin/env bash
# Run on the authorized Proxmox node. Creates a NEW VM only; never replaces one.
set -Eeuo pipefail
vmid=${1:?Usage: provision-repository-vm.sh NEW_VMID PUBLIC_KEY_FILE VENDOR_DATA_FILE}
public_key=${2:?Missing SSH public key path}
vendor_data=${3:?Missing cloud-init vendor data path}
vm_storage=${ROUGAROU_VM_STORAGE:?Set ROUGAROU_VM_STORAGE to a VM disk storage pool}
snippet_storage=${ROUGAROU_SNIPPET_STORAGE:?Set ROUGAROU_SNIPPET_STORAGE to storage supporting snippets}
bridge=${ROUGAROU_BRIDGE:-vmbr0}
for value in "$vm_storage" "$snippet_storage" "$bridge"; do
  [[ $value =~ ^[A-Za-z][A-Za-z0-9_.-]*$ ]] || { echo "Invalid storage or bridge identifier" >&2; exit 1; }
done
[[ $vmid =~ ^[1-9][0-9]{2,8}$ ]] || { echo 'Invalid VM ID' >&2; exit 1; }
[[ -f $public_key && -f $vendor_data ]] || { echo 'Input file missing' >&2; exit 1; }
if qm config "$vmid" >/dev/null 2>&1; then
  echo "VM $vmid already exists; refusing to modify it." >&2
  exit 1
fi
pvesh get /cluster/nextid --vmid "$vmid" >/dev/null
available_kb=$(awk '/^MemAvailable:/ {print $2}' /proc/meminfo)
(( available_kb >= 4 * 1024 * 1024 )) || { echo 'Less than 4GiB memory available' >&2; exit 1; }
ssh-keygen -lf "$public_key" >/dev/null

cloud_name=debian-13-genericcloud-amd64-20260914-2601.qcow2
cloud_url="https://cloud.debian.org/images/cloud/trixie/20260914-2601/$cloud_name"
cloud_sha512=95e110dfcdbd0ed8a82a75ed9579802f9950cabf51a810dcc6388e81bc778188713878b9f28d583a0ea602fbf48b35996ae9ad37f584166d8fbd6489df248f53
work=/var/lib/vz/rougarou-bootstrap
install -d -m 0755 "$work"
if [[ ! -f $work/$cloud_name ]]; then
  curl --fail --location --retry 4 --connect-timeout 30 "$cloud_url" -o "$work/$cloud_name.part"
  mv "$work/$cloud_name.part" "$work/$cloud_name"
fi
printf '%s  %s\n' "$cloud_sha512" "$work/$cloud_name" | sha512sum -c -
# Debian's current cloud checksums are delivered over TLS without a detached
# GPG signature. This pins that verified checksum; do not claim GPG verification.
snippet=$(pvesm path "$snippet_storage:snippets/rougarou-repo-$vmid.yaml")
install -d -m 0755 "$(dirname -- "$snippet")"
[[ ! -e $snippet ]] || { echo 'Cloud-init snippet already exists; refusing overwrite' >&2; exit 1; }
install -m 0644 "$vendor_data" "$snippet"

qm create "$vmid" --name rougarou-repo --description 'Rougarou OS signed package repository; LAN only; no signing keys' \
  --tags rougarou --memory 2048 --balloon 1024 --cores 2 --cpu x86-64-v2-AES \
  --ostype l26 --scsihw virtio-scsi-single --net0 "virtio,bridge=$bridge" \
  --serial0 socket --vga serial0 --agent enabled=1 --onboot 1
qm importdisk "$vmid" "$work/$cloud_name" "$vm_storage" --format raw
disk=$(qm config "$vmid" | sed -n 's/^unused0: //p')
[[ $disk == "$vm_storage":vm-"$vmid"-disk-* ]] || { echo 'Unexpected imported disk; inspect new VM' >&2; exit 1; }
qm set "$vmid" --scsi0 "$disk,discard=on,iothread=1" --ide2 "$vm_storage:cloudinit" \
  --boot order=scsi0 --ciuser rougarou --sshkeys "$public_key" --ipconfig0 ip=dhcp \
  --ciupgrade 0 \
  --cicustom "vendor=$snippet_storage:snippets/rougarou-repo-$vmid.yaml"
qm resize "$vmid" scsi0 32G
qm cloudinit update "$vmid"
qm start "$vmid"
qm config "$vmid"
printf '\nNew VM %s started. Wait for cloud-init and obtain IP with qm guest cmd %s network-get-interfaces.\n' "$vmid" "$vmid"
