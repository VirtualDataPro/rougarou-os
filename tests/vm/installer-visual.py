#!/usr/bin/env python3
"""Manual visual QA for the real installer in a disposable, offline QEMU VM.

Run `start` inside the VM-test container with /evidence writable; control the
local QMP/serial sockets with `key` and `capture`. No real disk device is used.
Screenshots are QEMU screendumps, not mockups. Keep temporary credentials out
of screenshots and never publish the disposable disk or private serial log.
"""
import argparse
import json
import re
from pathlib import Path
import shutil
import socket
import subprocess
import time


def qmp(path, command, arguments=None):
    with socket.socket(socket.AF_UNIX) as channel:
        channel.settimeout(10)
        channel.connect(str(path))
        stream = channel.makefile("rwb", buffering=0)
        json.loads(stream.readline())
        for request in ({"execute": "qmp_capabilities", "id": 1},
                        {"execute": command, "arguments": arguments or {}, "id": 2}):
            stream.write(json.dumps(request).encode() + b"\n")
            while True:
                result = json.loads(stream.readline())
                if result.get("id") != request["id"]:
                    continue
                if "error" in result:
                    raise RuntimeError(result["error"])
                if request["id"] == 2:
                    return result.get("return")
                break


def start(args):
    output = args.output.resolve()
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    disk = output / "DISPOSABLE.qcow2"
    subprocess.run(["qemu-img", "create", "-f", "qcow2", str(disk), "16G"], check=True)
    command = ["qemu-system-x86_64", "-enable-kvm", "-machine", "q35", "-cpu", "host",
               "-m", "2048", "-smp", "2", "-display", "none", "-vga", "std",
               "-monitor", "none", "-nic", "none", "-device", "virtio-net-pci", "-no-reboot",
               "-qmp", f"unix:{output}/qmp.sock,server=on,wait=off",
               "-chardev", f"socket,id=serial0,path={output}/serial.sock,server=on,wait=off,logfile={output}/serial.log",
               "-serial", "chardev:serial0", "-drive", f"file={disk},format=qcow2,if=virtio",
               "-boot", "d", "-cdrom", str(args.iso.resolve())]
    if args.firmware == "uefi":
        variables = output / "OVMF_VARS.fd"
        shutil.copyfile("/usr/share/OVMF/OVMF_VARS_4M.fd", variables)
        command += ["-drive", "if=pflash,format=raw,readonly=on,file=/usr/share/OVMF/OVMF_CODE_4M.fd",
                    "-drive", f"if=pflash,format=raw,file={variables}"]
    if args.initrd:
        if not args.kernel:
            raise ValueError("Provisional initrd testing requires --kernel")
        console = "tty0" if args.console == "vga" else "ttyS0,115200n8"
        command += ["-kernel", str(args.kernel.resolve()), "-initrd", str(args.initrd.resolve()),
                    "-append", f"vga=normal nomodeset fb=false console={console} preseed/file=/cdrom/rougarou/preseed.cfg --- quiet"]
    (output / "command.json").write_text(json.dumps(command, indent=2) + "\n")
    (output / "console").write_text(args.console)
    with (output / "qemu.log").open("w") as log:
        guest = subprocess.Popen(command, stdout=log, stderr=log)
        print(f"Visual QA VM started: {output}", flush=True)
        try:
            while guest.poll() is None and not (output / "STOP").exists():
                time.sleep(1)
        finally:
            guest.terminate()
            try:
                guest.wait(timeout=10)
            except subprocess.TimeoutExpired:
                guest.kill()
                guest.wait()


def key(args):
    output = args.output.resolve()
    if (output / "console").read_text() == "serial":
        keys = {"ret": b"\r", "tab": b"\t", "up": b"\x1b[A", "down": b"\x1b[B",
                "left": b"\x1b[D", "right": b"\x1b[C", "esc": b"\x1b", "spc": b" ",
                "ctrl-a": b"\x01", "backspace": b"\x7f", "ctrl-alt-f1": b"\x011",
                "ctrl-alt-f2": b"\x012", "ctrl-alt-f4": b"\x014"}
        with socket.socket(socket.AF_UNIX) as channel:
            channel.connect(str(output / "serial.sock"))
            channel.sendall(keys[args.key])
            time.sleep(0.2)
    else:
        qmp(output / "qmp.sock", "human-monitor-command", {"command-line": f"sendkey {args.key}"})


def capture(args):
    output = args.output.resolve()
    # QEMU resolves this path in its own filesystem. Container runs should use
    # the same /evidence mount path when calling this command.
    image = output / f"{args.name}.ppm"
    qmp(output / "qmp.sock", "screendump", {"filename": str(image)})
    print(image)


def public_text(args):
    """Only short public fixture identifiers; never use this for passwords."""
    if not re.fullmatch(r"[a-z0-9-]{1,32}", args.text):
        raise ValueError("Expected a short public fixture identifier")
    output = args.output.resolve()
    if (output / "console").read_text() == "serial":
        with socket.socket(socket.AF_UNIX) as channel:
            channel.connect(str(output / "serial.sock"))
            channel.sendall(args.text.encode())
    else:
        for letter in args.text:
            code = "minus" if letter == "-" else letter
            qmp(output / "qmp.sock", "human-monitor-command", {"command-line": f"sendkey {code}"})
            time.sleep(0.12)


parser = argparse.ArgumentParser(description=__doc__)
commands = parser.add_subparsers(dest="command", required=True)
launch = commands.add_parser("start")
launch.add_argument("--iso", type=Path, required=True)
launch.add_argument("--output", type=Path, required=True)
launch.add_argument("--console", choices=("vga", "serial"), default="vga")
launch.add_argument("--firmware", choices=("bios", "uefi"), default="bios")
launch.add_argument("--initrd", type=Path)
launch.add_argument("--kernel", type=Path)
press = commands.add_parser("key")
press.add_argument("--output", type=Path, required=True)
press.add_argument("key", choices=("ret", "tab", "up", "down", "left", "right", "esc", "spc", "ctrl-a", "backspace", "ctrl-alt-f1", "ctrl-alt-f2", "ctrl-alt-f4"))
typing = commands.add_parser("public-text")
typing.add_argument("--output", type=Path, required=True)
typing.add_argument("text")
shot = commands.add_parser("capture")
shot.add_argument("--output", type=Path, required=True)
shot.add_argument("--name", choices=("boot", "welcome", "language", "keyboard", "access", "back", "storage", "error"), required=True)
args = parser.parse_args()
{"start": start, "key": key, "capture": capture, "public-text": public_text}[args.command](args)
