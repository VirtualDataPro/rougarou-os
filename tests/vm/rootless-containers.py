#!/usr/bin/python3
"""Run only as an ordinary account in a disposable Rougarou acceptance VM.

Imports a tiny local ELF/shell filesystem into Docker and Podman; no registry,
network, AI request or preloaded external image is used. Removes only its own
unique images. The new Docker service/context remain configured for inspection.
Use --docker-only to verify rootless Docker with Podman absent.
"""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import tempfile
import uuid


def run(argv):
    result = subprocess.run(argv, capture_output=True, text=True, timeout=120)
    if result.returncode:
        raise RuntimeError(f"{argv[0]} failed: {result.stderr}")
    return result.stdout.strip()


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--docker-only", action="store_true", help="Require Podman to be absent and exercise Docker alone")
arguments = parser.parse_args()
engines = ("docker",) if arguments.docker_only else ("docker", "podman")

if os.getuid() == 0:
    raise SystemExit("Run this acceptance test as the disposable VM's ordinary operator.")

run(["rougarou-docker", "setup"])
run(["rougarou-docker", "setup"])
info = json.loads(run(["rougarou-docker", "status", "--json"]))
assert info["rootless"] is True
pid = int(run(["systemctl", "--user", "show", "rougarou-docker.service", "--property=MainPID", "--value"]))
assert Path(f"/proc/{pid}").stat().st_uid == os.getuid()
podman_runtime = None
if arguments.docker_only:
    assert shutil.which("podman") is None, "Docker-only acceptance requires Podman to be absent"
else:
    podman_host = json.loads(run(["podman", "info", "--format", "json"]))["host"]
    assert podman_host["security"]["rootless"] is True
    podman_runtime = {key: podman_host["ociRuntime"][key] for key in ("name", "path")}
assert run(["docker", "context", "show"]) == "rougarou-rootless", "Use a fresh acceptance account without prior Docker configuration"
assert "name=rootless" in json.loads(run(["docker", "info", "--format", "{{json .SecurityOptions}}"])), "Ordinary docker commands must select the rootless daemon"
assert "docker" not in run(["id", "-Gn"]).split(), "Operator must not belong to the root-equivalent Docker group"
for unit in ("docker.service", "docker.socket", "containerd.service"):
    state = subprocess.run(["systemctl", "is-enabled", unit], capture_output=True, text=True)
    assert state.stdout.strip() == "masked", f"Unexpected system unit state: {unit}"
    state = subprocess.run(["systemctl", "is-active", unit], capture_output=True, text=True)
    assert state.stdout.strip() == "inactive", f"Unexpected active system runtime: {unit}"

tag = "localhost/rougarou-offline-test:" + uuid.uuid4().hex
results = {}
with tempfile.TemporaryDirectory(prefix="rougarou-container-test-") as directory:
    work = Path(directory)
    archive = work / "rootfs.tar"
    shell = Path("/usr/bin/dash")
    dependencies = set(re.findall(r"(/[A-Za-z0-9_./+\-]+)", run(["ldd", str(shell)])))
    with tarfile.open(archive, "w") as tar:
        tar.add(shell.resolve(), arcname="bin/sh", recursive=False)
        for name in sorted(dependencies):
            path = Path(name)
            if path.is_file():
                tar.add(path.resolve(), arcname=name.lstrip("/"), recursive=False)
    for engine in engines:
        command = [engine, "--context", "rougarou-rootless"] if engine == "docker" else [engine]
        proof = work / engine
        proof.mkdir()
        imported = False
        try:
            run(command + ["import", str(archive), tag])
            imported = True
            output = run(command + ["run", "--rm", "--pull=never", "--network=none", "--mount", f"type=bind,source={proof},target=/proof", tag, "/bin/sh", "-c", "printf 'ROUGAROU_OFFLINE_CONTAINER_OK\\n'; printf 'owned-by-operator\\n' > /proof/result"])
            assert output == "ROUGAROU_OFFLINE_CONTAINER_OK", output
            assert (proof / "result").read_text() == "owned-by-operator\n"
            assert (proof / "result").stat().st_uid == os.getuid()
            results[engine] = "offline payload ran; host output owned by ordinary operator"
        finally:
            if imported:
                run(command + ["image", "rm", tag])
print(json.dumps({"result": "PASS", "operator_uid": os.getuid(), "docker": info,
                  "podman_expected": not arguments.docker_only,
                  "podman_oci_runtime": podman_runtime, "containers": results}, indent=2))
