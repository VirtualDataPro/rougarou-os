#!/usr/bin/env bash
# Run only on the authorized Proxmox node; create a NEW disposable VM.
set -Eeuo pipefail
[[ $# == 4 ]] || { echo 'Usage: provision-cloud-test.sh NEW_VMID QCOW2 EXPECTED_SHA256 PUBLIC_KEY' >&2; exit 2; }
vmid=$1; image=$2; expected=$3; public_key=$4
vm_storage=${ROUGAROU_VM_STORAGE:?Set ROUGAROU_VM_STORAGE to a VM disk storage pool}
bridge=${ROUGAROU_BRIDGE:-vmbr0}
[[ $vm_storage =~ ^[A-Za-z][A-Za-z0-9_.-]*$ && $bridge =~ ^[A-Za-z][A-Za-z0-9_.-]*$ ]]
[[ $vmid =~ ^[1-9][0-9]{2,8}$ ]]
[[ $expected =~ ^[a-f0-9]{64}$ && -f $image && -f $public_key ]]
if qm config "$vmid" >/dev/null 2>&1; then
  echo 'VM already exists; refusing to modify it.' >&2; exit 1
fi
pvesh get /cluster/nextid --vmid "$vmid" >/dev/null
printf '%s  %s\n' "$expected" "$image" | sha256sum -c -
ssh-keygen -lf "$public_key" >/dev/null
available_kb=$(awk '/^MemAvailable:/ {print $2}' /proc/meminfo)
(( available_kb >= 4 * 1024 * 1024 ))
qm create "$vmid" --name "rougarou-cloud-test-$vmid" --description 'Disposable Rougarou cloud image and recovery acceptance; no personal data' \
  --memory 2048 --cores 2 --cpu x86-64-v2-AES --machine q35 --bios ovmf --ostype l26 \
  --scsihw virtio-scsi-single --efidisk0 "$vm_storage:1,efitype=4m,pre-enrolled-keys=0" \
  --net0 "virtio,bridge=$bridge" --agent enabled=1 --serial0 socket --vga std --onboot 0 --tags 'rougarou;disposable;cloud-test'
qm importdisk "$vmid" "$image" "$vm_storage" --format raw
unused=$(qm config "$vmid" | sed -n 's/^unused0: //p')
[[ $unused == "$vm_storage":vm-"$vmid"-disk-* ]]
qm set "$vmid" --scsi0 "$unused,discard=on,iothread=1" --ide2 "$vm_storage:cloudinit" --boot order=scsi0 \
  --ciuser cloudtest --sshkeys "$public_key" --ipconfig0 ip=dhcp --ciupgrade 0
qm resize "$vmid" scsi0 16G
qm cloudinit update "$vmid"
qm start "$vmid"
qm config "$vmid"
