#!/usr/bin/env python3
"""Exercise an explicitly selected system Docker daemon, offline and locally."""
import json
import os
from pathlib import Path
import re
import subprocess
import tarfile
import tempfile
import uuid


def run(args):
    result = subprocess.run(args, capture_output=True, text=True, timeout=90)
    if result.returncode:
        raise RuntimeError(f'{args[0]} failed: {result.stderr}')
    return result.stdout.strip()


assert os.geteuid() == 0, 'Run only inside the disposable acceptance guest via QGA'
docker = ['docker', '--host', 'unix:///var/run/docker.sock']
info = json.loads(run(docker + ['info', '--format', '{{json .}}']))
assert not any(option.split(',')[0] == 'name=rootless' for option in info.get('SecurityOptions', []))
pid = int(run(['systemctl', 'show', 'docker.service', '--property=MainPID', '--value']))
assert pid > 0 and Path(f'/proc/{pid}').stat().st_uid == 0
assert 'docker' not in run(['id', '-Gn', 'tester']).split()
denied = subprocess.run(['runuser', '-u', 'tester', '--', *docker, 'info'], text=True, capture_output=True, timeout=20)
assert denied.returncode != 0, 'Ordinary operator unexpectedly controls root-owned Docker without sudo'
run(['runuser', '-u', 'tester', '--', 'sudo', '-n', '-k', '--', *docker, 'info', '--format', '{{.ServerVersion}}'])
tag = 'localhost/rougarou-system-test:' + uuid.uuid4().hex
with tempfile.TemporaryDirectory(prefix='rougarou-system-container-') as name:
    work = Path(name)
    archive = work / 'rootfs.tar'
    shell = Path('/usr/bin/dash')
    dependencies = set(re.findall(r'(/[A-Za-z0-9_./+\-]+)', run(['ldd', str(shell)])))
    with tarfile.open(archive, 'w') as stream:
        stream.add(shell.resolve(), arcname='bin/sh', recursive=False)
        for name in sorted(dependencies):
            path = Path(name)
            if path.is_file():
                stream.add(path.resolve(), arcname=name.lstrip('/'), recursive=False)
    imported = False
    try:
        run(docker + ['import', str(archive), tag])
        imported = True
        output = run(docker + ['run', '--rm', '--pull=never', '--network=none', tag,
                              '/bin/sh', '-c', 'printf ROUGAROU_SYSTEM_CONTAINER_OK'])
        assert output == 'ROUGAROU_SYSTEM_CONTAINER_OK'
    finally:
        if imported:
            run(docker + ['image', 'rm', tag])
print(json.dumps({'result': 'PASS', 'explicit_system_docker': True, 'daemon_uid': 0,
                  'operator_in_docker_group': False, 'ordinary_socket_access_denied': True,
                  'sudo_access_passed': True, 'offline_container_passed': True,
                  'version': info['ServerVersion']}, sort_keys=True))
