#!/usr/bin/env python3
"""Boot and install real ISO content, only inside network-isolated disposable VMs.

Test-specific preseeding is injected into a separate ISO copy, never the release.
The original release ISO is first booted to its installer independently for each
firmware. Guest assertions use QEMU's local virtio guest-agent channel, not SSH.
"""
import argparse
import base64
from concurrent.futures import ThreadPoolExecutor, as_completed
import gzip
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import threading
import time
import re

import pexpect
from screenshots import QMP, capture_boot_frames

ISO = Path("/input/rougarou.iso")
OUT = Path("/evidence")
RESULTS = {"iso_sha256": "", "started_utc": "", "tests": []}
IDENTITIES = {}
RESULT_LOCK = threading.Lock()
VERSIONS = dict(re.findall(r"^([A-Z_]+)=(.+)$", (Path(__file__).resolve().parents[2] / "versions.env").read_text(), re.M))
VERSION = VERSIONS['ROUGAROU_VERSION']
PACKAGE_VERSIONS = {
    'rougarou-base': VERSION.replace('-alpha.', '~alpha') + '-' + VERSIONS.get('ROUGAROU_BASE_REVISION', VERSIONS['ROUGAROU_PACKAGE_REVISION']),
    'rougarou-codex': VERSIONS['CODEX_VERSION'] + '+rougarou' + VERSIONS['ROUGAROU_PACKAGE_REVISION'],
    'rougarou-herdr': VERSIONS['HERDR_VERSION'] + '+rougarou' + VERSIONS['ROUGAROU_PACKAGE_REVISION'],
}
ACCESS_MODES = {"bios": "headless", "uefi": "password"}
ACCESS_MODES['bios-system'] = 'headless'
# These explicit test preseeds exercise package combinations; they do not prove
# the installer UI's highlighted defaults, which have separate native UI QA.
SOFTWARE_CHOICES = {
    'bios': {'agent': 'none', 'docker': 'rootless', 'podman': 'false', 'herdr': 'false'},
    'uefi': {'agent': 'codex', 'docker': 'rootless', 'podman': 'true', 'herdr': 'true'},
    'bios-system': {'agent': 'gemini', 'docker': 'system', 'podman': 'false', 'herdr': 'false'},
}
CASE_NAMES = tuple(SOFTWARE_CHOICES)
for agent in ('GEMINI',):
    if agent + '_VERSION' in VERSIONS:
        PACKAGE_VERSIONS['rougarou-' + agent.lower()] = VERSIONS[agent + '_VERSION'] + '+rougarou' + VERSIONS['ROUGAROU_PACKAGE_REVISION']


def case_firmware(case):
    return 'uefi' if case == 'uefi' else 'bios'


def selected_package_versions(case):
    selection = SOFTWARE_CHOICES[case]
    packages = {'rougarou-base': PACKAGE_VERSIONS['rougarou-base']}
    for name in (selection['agent'], 'herdr' if selection['herdr'] == 'true' else 'none'):
        if name not in ('none', 'claude', 'opencode'):
            packages['rougarou-' + name] = PACKAGE_VERSIONS['rougarou-' + name]
    return packages


def command(args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)


def record(name, passed, detail):
    with RESULT_LOCK:
        RESULTS["tests"].append({"name": name, "passed": passed, "detail": detail})
        (OUT / "results.json").write_text(json.dumps(RESULTS, indent=2) + "\n")
        print(f"{'PASS' if passed else 'FAIL'} {name}: {detail}", flush=True)


def firmware_args(firmware, directory):
    if firmware == "bios":
        return []
    code = Path("/usr/share/OVMF/OVMF_CODE_4M.fd")
    variables = directory / "OVMF_VARS.fd"
    if not variables.exists():
        shutil.copyfile("/usr/share/OVMF/OVMF_VARS_4M.fd", variables)
    return ["-drive", f"if=pflash,format=raw,readonly=on,file={code}", "-drive", f"if=pflash,format=raw,file={variables}"]


def qemu_base(firmware, directory):
    return [
        "qemu-system-x86_64", "-enable-kvm", "-machine", "q35", "-cpu", "host",
        "-m", "3072", "-smp", "2", "-display", "none", "-monitor", "none",
        "-nic", "none", "-no-reboot",
    ] + firmware_args(firmware, directory)


def boot_release(firmware):
    directory = OUT / f"{firmware}-release-boot"
    directory.mkdir()
    qmp_path = directory / 'qmp.sock'
    args = qemu_base(firmware, directory) + ["-boot", "d", "-cdrom", str(ISO), "-serial", "stdio",
                                           '-qmp', f'unix:{qmp_path},server=on,wait=off']
    (directory / "command.json").write_text(json.dumps(args, indent=2))
    with (directory / "serial.log").open("w") as log:
        guest = pexpect.spawn(args[0], args[1:], encoding="utf-8", codec_errors="replace", timeout=90)
        guest.logfile_read = log
        try:
            guest.expect(["R O U G A R O U", "ROUGAROU OS"])
            time.sleep(1)
            qmp = QMP(qmp_path)
            try:
                frame = qmp.screenshot(directory / 'original-menu.png')
                (directory / 'screenshot.json').write_text(json.dumps(frame, indent=2) + '\n')
            finally:
                qmp.close()
            guest.send("\x1b[B\r")
            guest.expect("A quiet server", timeout=120)
            guest.send("\r")
            guest.expect(["Select a language", "Choose a language", "Language:"], timeout=120)
            record(f"{firmware}-release-iso-boot", True, "Original ISO menu, Rougarou welcome and native language choice visible over serial.")
        finally:
            guest.terminate(force=True)


def prepare_test_iso(firmware):
    work = OUT / f"test-only-media-{firmware}"
    work.mkdir()
    initrd = work / "initrd.gz"
    with (work / "xorriso-extract.log").open("w") as log:
        command(["xorriso", "-osirrox", "on", "-indev", str(ISO), "-extract", "/install.amd/initrd.gz", str(initrd)], stdout=log, stderr=log)
    seed_root = work / "seed"
    seed_root.mkdir()
    password = secrets.token_urlsafe(24)
    password_hash = command(["openssl", "passwd", "-6", "-stdin"], input=password + "\n", text=True, capture_output=True).stdout.strip()
    # Only the disposable guest receives this password. No credential touches
    # the production ISO, repository, log, or command arguments.
    (work / "test-password").write_text(password)
    (work / "test-password").chmod(0o600)
    seed = f"""# TEST ONLY. Destructive settings target this harness's fresh /dev/vda.
d-i debian-installer/locale string en_US.UTF-8
d-i keyboard-configuration/xkb-keymap select us
d-i netcfg/enable boolean false
d-i netcfg/get_hostname string rougarou-vm-test
d-i netcfg/get_domain string test.invalid
d-i hw-detect/load_firmware boolean false
d-i passwd/root-login boolean false
d-i passwd/user-fullname string Disposable VM Test
d-i passwd/username string tester
d-i passwd/user-password-crypted password {password_hash}
d-i rougarou/welcome seen true
d-i rougarou/operator-access select {ACCESS_MODES[firmware]}
d-i rougarou/agent select {SOFTWARE_CHOICES[firmware]['agent']}
d-i rougarou/docker select {SOFTWARE_CHOICES[firmware]['docker']}
d-i rougarou/podman boolean {SOFTWARE_CHOICES[firmware]['podman']}
d-i rougarou/herdr boolean {SOFTWARE_CHOICES[firmware]['herdr']}
d-i clock-setup/utc boolean true
d-i clock-setup/ntp boolean false
d-i time/zone string Etc/UTC
d-i partman-auto/disk string /dev/vda
d-i partman-auto/method string regular
d-i partman-auto/choose_recipe select atomic
d-i partman-lvm/device_remove_lvm boolean true
d-i partman-md/device_remove_md boolean true
d-i partman-partitioning/confirm_write_new_label boolean true
d-i partman/choose_partition select finish
d-i partman/confirm boolean true
d-i partman/confirm_nooverwrite boolean true
d-i partman-efi/non_efi_system boolean true
d-i apt-setup/use_mirror boolean false
d-i apt-setup/cdrom/set-first boolean false
d-i apt-setup/services-select multiselect
d-i pkgsel/run_tasksel boolean false
d-i pkgsel/upgrade select none
tasksel tasksel/first multiselect
popularity-contest popularity-contest/participate boolean false
d-i grub-installer/only_debian boolean true
d-i grub-installer/with_other_os boolean true
d-i grub-installer/bootdev string /dev/vda
d-i grub-installer/force-efi-extra-removable boolean true
d-i finish-install/reboot_in_progress note
d-i debian-installer/exit/poweroff boolean true
d-i preseed/late_command string /bin/sh /cdrom/rougarou/install-target.sh
"""
    (seed_root / "preseed.cfg").write_text(seed)
    (seed_root / "preseed.cfg").chmod(0o600)
    # Concatenated gzip members and cpio archives are supported by Linux initramfs.
    archive = command(["cpio", "-o", "-H", "newc"], cwd=seed_root, input=b"preseed.cfg\n", capture_output=True).stdout
    with initrd.open("ab") as stream:
        stream.write(gzip.compress(archive, mtime=0))
    # A serial kernel console makes Debian deliberately configure serial GRUB.
    # These fresh installs test the ordinary VGA boot-menu defaults; original
    # release-media serial boot coverage remains separate in boot_release().
    kernel_args = "auto=true priority=critical DEBIAN_FRONTEND=text console=tty0 --- console=tty0"
    (work / "isolinux.cfg").write_text(f"default test\nprompt 0\ntimeout 1\nserial 0 115200\nlabel test\n kernel /install.amd/vmlinuz\n append initrd=/install.amd/initrd.gz {kernel_args}\n")
    (work / "grub.cfg").write_text(f"set default=0\nset timeout=0\nserial --unit=0 --speed=115200\nterminal_input serial\nterminal_output serial\nmenuentry 'Disposable Rougarou validation only' {{\n linux /install.amd/vmlinuz {kernel_args}\n initrd /install.amd/initrd.gz\n}}\n")
    test_iso = work / "TEST-ONLY-rougarou.iso"
    with (work / "xorriso-repack.log").open("w") as log:
        command(["xorriso", "-indev", str(ISO), "-outdev", str(test_iso), "-boot_image", "any", "replay", "-map", str(initrd), "/install.amd/initrd.gz", "-map", str(work / "isolinux.cfg"), "/isolinux/isolinux.cfg", "-map", str(work / "grub.cfg"), "/boot/grub/grub.cfg", "-commit", "-end"], stdout=log, stderr=log)
    return test_iso


def guest_call(channel, execute, arguments=None):
    request_id = secrets.randbits(31)
    request = {"execute": execute, "id": request_id}
    if arguments is not None:
        request["arguments"] = arguments
    channel.sendall(json.dumps(request).encode() + b"\n")
    response = b""
    while True:
        block = channel.recv(65536)
        if not block:
            raise RuntimeError("Guest agent connection ended.")
        response += block
        while b"\n" in response:
            line, response = response.split(b"\n", 1)
            result = json.loads(line)
            if result.get("id") != request_id:
                continue
            if "error" in result:
                raise RuntimeError(result["error"])
            return result["return"]


def render_guest_script(firmware):
    shell = (Path(__file__).resolve().parent / "guest-assertions.sh").read_text()
    firmware_check = "test -d /sys/firmware/efi\n" if case_firmware(firmware) == "uefi" else "test ! -d /sys/firmware/efi\n"
    shell = "set -eu\n" + firmware_check + shell
    shell = shell.replace("@VERSION@", VERSION).replace("@ACCESS@", ACCESS_MODES[firmware])
    shell = shell.replace("@PACKAGE_VERSIONS_JSON@", json.dumps(selected_package_versions(firmware)))
    shell = shell.replace("@SOFTWARE_CHOICES_JSON@", json.dumps(SOFTWARE_CHOICES[firmware]))
    for key, value in SOFTWARE_CHOICES[firmware].items():
        shell = shell.replace("@" + key.upper() + "@", value)
    shell_tests = Path(__file__).resolve().parents[1] / "test_shell.py"
    shell = shell.replace("@SHELL_TEST_BASE64@", base64.b64encode(shell_tests.read_bytes()).decode())
    codex_test = Path(__file__).resolve().parent / "codex-keyboard-check.py"
    shell = shell.replace("@CODEX_KEYBOARD_TEST_BASE64@", base64.b64encode(codex_test.read_bytes()).decode())
    herdr_test = Path(__file__).resolve().parent / "herdr-smoke.py"
    shell = shell.replace("@HERDR_TEST_BASE64@", base64.b64encode(herdr_test.read_bytes()).decode())
    container_test = Path(__file__).resolve().parent / "rootless-containers.py"
    shell = shell.replace("@ROOTLESS_TEST_BASE64@", base64.b64encode(container_test.read_bytes()).decode())
    for label, filename in (("GRUB", "grub-check.py"), ("TIMING", "installer-timings.py"),
                            ("SYSTEM_DOCKER", "system-docker.py"),
                            ("OPERATOR_FEATURES", "operator-features.py")):
        test_file = Path(__file__).resolve().parent / filename
        shell = shell.replace("@" + label + "_TEST_BASE64@", base64.b64encode(test_file.read_bytes()).decode())
    return shell


def verify_installed(firmware, directory, disk):
    qga = directory / "qga.sock"
    qmp_path = directory / 'installed-qmp.sock'
    args = qemu_base(case_firmware(firmware), directory) + [
        "-boot", "c", "-drive", f"file={disk},format=qcow2,if=virtio",
        "-serial", f"file:{directory / 'installed-serial.log'}",
        "-device", "virtio-serial-pci", "-chardev", f"socket,path={qga},server=on,wait=off,id=qga0",
        "-device", "virtserialport,chardev=qga0,name=org.qemu.guest_agent.0",
        '-qmp', f'unix:{qmp_path},server=on,wait=off',
    ]
    (directory / "installed-command.json").write_text(json.dumps(args, indent=2))
    with (directory / "installed-qemu.log").open("w") as log:
        guest = subprocess.Popen(args, stdout=log, stderr=log)
        try:
            frame_directory = directory / 'native-boot-frames'
            if frame_directory.exists():
                frame_directory = directory / ('native-boot-frames-recheck-' + str(time.time_ns()))
            capture_boot_frames(qmp_path, frame_directory)
            deadline = time.monotonic() + 180
            channel = None
            while time.monotonic() < deadline:
                if guest.poll() is not None:
                    raise RuntimeError("Installed VM stopped before its guest agent became available.")
                try:
                    channel = socket.socket(socket.AF_UNIX)
                    channel.settimeout(3)
                    channel.connect(str(qga))
                    guest_call(channel, "guest-ping")
                    break
                except (OSError, ValueError):
                    if channel:
                        channel.close()
                    channel = None
                    time.sleep(2)
            if channel is None:
                raise RuntimeError("Installed guest did not expose its QEMU agent within 180 seconds.")
            shell = render_guest_script(firmware)
            started = guest_call(channel, "guest-exec", {"path": "/bin/sh", "arg": ["-c", shell], "capture-output": True})
            for _ in range(300):
                status = guest_call(channel, "guest-exec-status", {"pid": started["pid"]})
                if status.get("exited"):
                    break
                time.sleep(1)
            else:
                raise RuntimeError("Guest assertions timed out.")
            stdout = base64.b64decode(status.get("out-data", "")).decode(errors="replace")
            stderr = base64.b64decode(status.get("err-data", "")).decode(errors="replace")
            visible = []
            for line in stdout.splitlines():
                if line.startswith("IDENTITY_FACTS="):
                    IDENTITIES[firmware] = json.loads(line.removeprefix("IDENTITY_FACTS="))
                elif line.startswith("INSTALL_TIMINGS="):
                    facts = json.loads(line.removeprefix("INSTALL_TIMINGS="))
                    facts['selection'] = SOFTWARE_CHOICES[firmware]
                    (directory / 'installer-timings.json').write_text(json.dumps(facts, indent=2) + '\n')
                    visible.append('INSTALLER_STAGE_TIMINGS_COLLECTED')
                else:
                    visible.append(line)
            (directory / "guest-assertions.log").write_text("\n".join(visible) + "\nSTDERR:\n" + stderr)
            if status.get("exitcode") != 0 or "ROUGAROU_VM_ASSERTIONS_PASSED" not in stdout:
                raise RuntimeError(f"Guest assertions failed (exit {status.get('exitcode')}); inspect guest-assertions.log.")
            # guest-shutdown deliberately has no success response: the guest
            # agent's connection ends as the system powers off.
            channel.sendall(json.dumps({"execute": "guest-shutdown", "arguments": {"mode": "powerdown"}}).encode() + b"\n")
            channel.close()
            guest.wait(timeout=30)
            record(f"{firmware}-installed-system", True, "Offline install booted from disk; OS, CLI, SSH, guest agent, operator boundaries and agent-driven shutdown passed.")
        finally:
            guest.terminate()
            try:
                guest.wait(timeout=15)
            except subprocess.TimeoutExpired:
                guest.kill()
                guest.wait()


def install(firmware, test_iso):
    directory = OUT / f"{firmware}-install"
    directory.mkdir()
    disk = directory / "disposable.qcow2"
    command(["qemu-img", "create", "-f", "qcow2", str(disk), "16G"], capture_output=True)
    args = qemu_base(case_firmware(firmware), directory) + [
        "-boot", "d", "-cdrom", str(test_iso), "-drive", f"file={disk},format=qcow2,if=virtio",
        "-chardev", f"socket,id=serial0,path={directory / 'installer-console.sock'},server=on,wait=off,logfile={directory / 'installer-serial.log'}",
        "-serial", "chardev:serial0",
    ]
    (directory / "installer-command.json").write_text(json.dumps(args, indent=2))
    started = time.monotonic()
    with (directory / "installer-qemu.log").open("w") as log:
        try:
            command(args, stdout=log, stderr=log, timeout=900)
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"{firmware} offline installation exceeded 15 minutes; inspect installer-serial.log.") from None
    record(f"{firmware}-offline-install", True, f"Installer powered off after {int(time.monotonic() - started)} seconds with networking disabled.")
    verify_installed(firmware, directory, disk)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--verify-existing", action="store_true", help="Recheck already installed disks after a harness-only correction; preserve earlier results.")
    arguments = parser.parse_args()
    os.umask(0o077)
    RESULTS["started_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    RESULTS['software_choices'] = SOFTWARE_CHOICES
    with ISO.open("rb") as stream:
        RESULTS["iso_sha256"] = hashlib.file_digest(stream, "sha256").hexdigest()
    (OUT / "host-tools.txt").write_text(command(["qemu-system-x86_64", "--version"], text=True, capture_output=True).stdout)
    if arguments.verify_existing:
        if (OUT / "DIAGNOSTIC_ONLY").exists():
            raise RuntimeError("Diagnostic guests cannot be reused for release acceptance.")
        previous = json.loads((OUT / "results.json").read_text())
        if previous["iso_sha256"] != RESULTS["iso_sha256"]:
            raise RuntimeError("The installed-disk evidence belongs to a different ISO hash.")
        attempt = str(time.time_ns())
        shutil.copyfile(OUT / "results.json", OUT / f"results-before-runtime-recheck-{attempt}.json")
        for firmware in CASE_NAMES:
            assertions = OUT / f"{firmware}-install" / "guest-assertions.log"
            if assertions.exists():
                assertions.rename(assertions.with_name(f"guest-assertions-before-recheck-{attempt}.log"))
        RESULTS.update(previous)
        RESULTS["tests"] = [test for test in previous["tests"] if test["name"].endswith(("release-iso-boot", "offline-install")) and test["passed"]]
        if len(RESULTS["tests"]) != 2 + len(CASE_NAMES):
            raise RuntimeError("Both original-media boots and all selected clean installs must already have passed.")
        RESULTS["runtime_rechecked_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        RESULTS["runtime_recheck_note"] = "Same installed disks after a harness-only correction; prior results/logs retained. First-login dismissal is preserved, and existing queued test history is not replayed."
        test_iso = None
    else:
        for firmware in ("bios", "uefi"):
            try:
                boot_release(firmware)
            except Exception as error:
                record(f"{firmware}-release-iso-boot", False, str(error))
                raise
        test_isos = {firmware: prepare_test_iso(firmware) for firmware in CASE_NAMES}
    errors = []
    # Separate disks, firmware variables and sockets; only result writes share
    # state, under a lock. Six GiB total guest RAM is required for this stage.
    with ThreadPoolExecutor(max_workers=2) as executor:
        pending = {}
        for firmware in CASE_NAMES:
            if arguments.verify_existing:
                directory = OUT / f"{firmware}-install"
                future = executor.submit(verify_installed, firmware, directory, directory / "disposable.qcow2")
            else:
                future = executor.submit(install, firmware, test_isos[firmware])
            pending[future] = firmware
        for finished in as_completed(pending):
            firmware = pending[finished]
            try:
                finished.result()
            except Exception as error:
                record(f"{firmware}-offline-install-or-reboot", False, str(error))
                errors.append(f"{firmware}: {error}")
    if errors:
        raise RuntimeError("; ".join(errors))
    unique = all(len({IDENTITIES[case][key] for case in CASE_NAMES}) == len(CASE_NAMES) for key in ('machine', 'ssh_public'))
    record("fresh-install-identities", unique, "Compared all installed guests: machine IDs and SSH host public keys must differ.")
    if not unique:
        raise RuntimeError("Installed guest identities were not unique.")
    RESULTS["finished_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    (OUT / "results.json").write_text(json.dumps(RESULTS, indent=2) + "\n")


if __name__ == "__main__":
    main()
