#!/usr/bin/env python3
"""Offline, ordinary-user Herdr server lifecycle; no agents or model prompts."""
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile
import time
import uuid


if os.geteuid() == 0:
    raise SystemExit('Run the Herdr smoke test as the disposable ordinary operator')
binary = shutil.which('herdr')
assert binary, 'Pinned Herdr must be installed'
with tempfile.TemporaryDirectory(prefix='rg-herdr-') as directory:
    home = Path(directory)
    config = home / '.config/herdr'
    config.mkdir(parents=True, mode=0o700)
    (config / 'config.toml').write_text('[update]\nversion_check = false\nmanifest_check = false\n')
    runtime = home / 'run'
    runtime.mkdir(mode=0o700)
    environment = {'PATH': '/usr/local/bin:/usr/bin:/bin', 'HOME': str(home),
                   'SHELL': '/bin/sh', 'TERM': 'xterm-256color', 'LC_ALL': 'C.UTF-8',
                   'XDG_CONFIG_HOME': str(home / '.config'),
                   'XDG_DATA_HOME': str(home / '.local/share'),
                   'XDG_STATE_HOME': str(home / '.local/state'),
                   'XDG_CACHE_HOME': str(home / '.cache'), 'XDG_RUNTIME_DIR': str(runtime)}
    session = 'rg-test-' + uuid.uuid4().hex[:8]
    command = [binary, '--session', session]

    def invoke(*arguments):
        return subprocess.run(command + list(arguments), env=environment, cwd=home,
                              capture_output=True, text=True, check=True, timeout=8)

    def status():
        return json.loads(invoke('status', 'server', '--json').stdout)

    assert status()['running'] is False
    with (home / 'server-output.log').open('w') as log:
        server = subprocess.Popen(command + ['server'], env=environment, cwd=home,
                                  stdout=log, stderr=log, start_new_session=True)
        try:
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                running = status()
                if running['running']:
                    break
                if server.poll() not in (None, 0):
                    raise AssertionError('Herdr server exited before becoming ready')
                time.sleep(.1)
            else:
                raise AssertionError('Herdr server did not become ready')
            assert running['session'] == session
            assert running['version'] == '0.9.1'
            invoke('server', 'stop')
            deadline = time.monotonic() + 10
            while status()['running']:
                if time.monotonic() >= deadline:
                    raise AssertionError('Herdr did not stop its unique test session')
                time.sleep(.1)
            server.wait(timeout=10)
            print(json.dumps({'result': 'PASS', 'version': running['version'],
                              'ordinary_uid': os.getuid(), 'named_server_started': True,
                              'named_server_stopped': True, 'agents_launched': 0,
                              'model_requests': 0}, sort_keys=True))
        finally:
            if server.poll() is None:
                subprocess.run(command + ['server', 'stop'], env=environment, cwd=home,
                               capture_output=True, timeout=8)
                try:
                    server.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(server.pid, signal.SIGKILL)
                    server.wait()
