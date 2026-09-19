#!/usr/bin/env python3
"""Read installed Debian update policy from a temporary snapshot of a test VM."""
import base64
import json
from pathlib import Path
import socket
import subprocess
import time

from validate import guest_call, qemu_base

directory = Path("/evidence/bios-install")
channel_path = Path("/evidence/policy-qga.sock")
args = qemu_base("bios", directory) + [
    "-boot", "c", "-drive", f"file={directory / 'disposable.qcow2'},format=qcow2,if=virtio,snapshot=on",
    "-serial", "file:/evidence/policy-serial.log", "-device", "virtio-serial-pci",
    "-chardev", f"socket,path={channel_path},server=on,wait=off,id=policy",
    "-device", "virtserialport,chardev=policy,name=org.qemu.guest_agent.0",
]
with Path("/evidence/policy-qemu.log").open("w") as log:
    guest = subprocess.Popen(args, stdout=log, stderr=log)
    channel = None
    try:
        for _ in range(40):
            try:
                channel = socket.socket(socket.AF_UNIX)
                channel.settimeout(3)
                channel.connect(str(channel_path))
                guest_call(channel, "guest-ping")
                break
            except (OSError, ValueError):
                if channel:
                    channel.close()
                channel = None
                time.sleep(1)
        if channel is None:
            raise RuntimeError("Guest agent unavailable")
        query = r'''apt-config dump | grep -E 'APT::Periodic|Unattended-Upgrade::(Origins|Allowed|Automatic)'
printf '\n=== periodic configuration files ===\n'
for config in /etc/apt/apt.conf.d/20auto-upgrades /etc/apt/apt.conf.d/10periodic; do
    if [ -f "$config" ]; then
        printf '%s\n' "$config"
        cat "$config"
    else
        printf 'ABSENT: %s\n' "$config"
    fi
done
printf '\n=== apt timer enablement ===\n'
systemctl is-enabled apt-daily.timer apt-daily-upgrade.timer
printf '\n=== unattended update debconf setting ===\n'
printf 'GET unattended-upgrades/enable_auto_updates\n' | DEBIAN_FRONTEND=noninteractive debconf-communicate unattended-upgrades
printf '\n=== apt daily defaults and configuration lookup ===\n'
grep -E '^(UpdateInterval|UnattendedUpgradeInterval)=|apt-config shell (UpdateInterval|UnattendedUpgradeInterval)' /usr/lib/apt/apt.systemd.daily
'''
        task = guest_call(channel, "guest-exec", {
            "path": "/bin/sh", "arg": ["-c", query],
            "capture-output": True,
        })
        for _ in range(20):
            result = guest_call(channel, "guest-exec-status", {"pid": task["pid"]})
            if result.get("exited"):
                break
            time.sleep(1)
        else:
            raise RuntimeError("Policy query timed out")
        output = base64.b64decode(result.get("out-data", "")).decode()
        errors = base64.b64decode(result.get("err-data", "")).decode()
        Path("/evidence/apt-policy.log").write_text(output + errors)
        print(output + errors, end="")
        if result.get("exitcode") != 0:
            raise RuntimeError("Policy query failed")
        channel.sendall(json.dumps({"execute": "guest-shutdown", "arguments": {"mode": "powerdown"}}).encode() + b"\n")
        channel.close()
        guest.wait(timeout=30)
    finally:
        if guest.poll() is None:
            guest.terminate()
            try:
                guest.wait(timeout=15)
            except subprocess.TimeoutExpired:
                guest.kill()
                guest.wait()
