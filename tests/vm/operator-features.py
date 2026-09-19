#!/usr/bin/python3
"""Installed alpha7 CLI acceptance in an offline disposable ordinary-user guest.

No provider calls, package refresh/apply, external webhooks, real operator config
or service enablement. --skip-installed-host-checks is for a disposable container
smoke only; release VM acceptance must omit that option.
"""
import argparse
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import pty
import select
import signal
import sqlite3
import subprocess
import tempfile
import threading
import time

CLI = '/usr/local/bin/rougarou'


def command(*arguments, env=None, allowed=(0,), timeout=20):
    result = subprocess.run([CLI, *arguments], env=env, stdin=subprocess.DEVNULL,
                            capture_output=True, text=True, timeout=timeout)
    if result.returncode not in allowed:
        # Fixture secrets can occur in child diagnostics; retain only the action
        # and exit code in shared acceptance output.
        raise AssertionError(f'Operator command {arguments[0]} failed with exit {result.returncode}')
    return result


def parsed(*arguments, env):
    return json.loads(command(*arguments, env=env).stdout)


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def installed_host_checks():
    home = Path.home()
    config = Path(os.environ.get('XDG_CONFIG_HOME', home / '.config')) / 'rougarou'
    assert not (config / 'notifications.json').exists(), 'Fresh operator already has notification credentials'
    assert not (config / 'backup.json').exists(), 'Fresh operator already has a backup destination'
    for unit in ('rougarou-notify.timer', 'rougarou-notify.service'):
        assert (Path('/usr/lib/systemd/user') / unit).is_file(), 'Notification user unit missing'
        result = subprocess.run(['systemctl', '--user', 'is-active', unit], capture_output=True, text=True, timeout=5)
        assert result.stdout.strip() == 'inactive', 'Notification unit unexpectedly active'
    result = subprocess.run(['systemctl', '--user', 'is-enabled', 'rougarou-notify.timer'],
                            capture_output=True, text=True, timeout=5)
    assert result.returncode != 0 and result.stdout.strip() == 'disabled', 'Notification timer unexpectedly enabled'
    result = subprocess.run(['systemctl', '--user', 'show', 'rougarou-notify.service',
                             '-p', 'NoNewPrivileges', '-p', 'MemoryMax', '-p', 'TasksMax', '-p', 'TimeoutStartUSec'],
                            capture_output=True, text=True, timeout=5, check=True)
    properties = dict(line.split('=', 1) for line in result.stdout.splitlines())
    assert properties['NoNewPrivileges'] == 'yes'
    assert int(properties['MemoryMax']) == 128 * 1024**2
    assert int(properties['TasksMax']) == 16
    assert properties['TimeoutStartUSec'] == '2min'
    return config


def menu_cancel(env, data):
    child, master = pty.fork()
    if child == 0:
        os.execve(CLI, [CLI, 'menu'], dict(env, TERM='xterm-256color'))
    output = bytearray()
    status = None
    sent = False
    deadline = time.monotonic() + 5
    try:
        while time.monotonic() < deadline:
            if select.select([master], [], [], .05)[0]:
                try:
                    block = os.read(master, 8192)
                except OSError:
                    block = b''
                output.extend(block)
            if not sent and b'Choose [0]:' in output:
                os.write(master, data)
                sent = True
            done, value = os.waitpid(child, os.WNOHANG)
            if done:
                status = value
                break
        assert sent and status is not None and os.waitstatus_to_exitcode(status) == 0, 'Installed terminal menu did not cancel cleanly'
        assert b'Traceback' not in output
    finally:
        os.close(master)
        if status is None:
            os.kill(child, signal.SIGKILL)
            os.waitpid(child, 0)


def isolated_operator_roundtrip():
    received = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            data = self.rfile.read(int(self.headers.get('Content-Length', 0)))
            received.append((dict(self.headers), data))
            self.send_response(204)
            self.send_header('Content-Length', '0')
            self.end_headers()

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    listener = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .02}, daemon=True)
    listener.start()
    worker = None
    try:
        with tempfile.TemporaryDirectory(prefix='rougarou-operator-acceptance-') as directory:
            root = Path(directory)
            home = root / 'isolated-home'
            home.mkdir(mode=0o700)
            env = {'PATH': '/usr/local/bin:/usr/bin:/bin', 'HOME': str(home), 'LC_ALL': 'C.UTF-8',
                   'XDG_CONFIG_HOME': str(home / '.config'), 'XDG_STATE_HOME': str(home / '.local/state'),
                   'XDG_CACHE_HOME': str(home / '.cache'), 'TERM': 'xterm-256color', 'PYTHONDONTWRITEBYTECODE': '1'}
            settings = home / '.config/rougarou'
            settings.mkdir(mode=0o700, parents=True)
            state = home / '.local/state/rougarou'
            credential = settings / 'credentials.json'
            credential.write_text('{"fixture_secret":"private-provider-acceptance"}\n')
            credential.chmod(0o600)
            service = home / 'Work/private-workspace'
            service.mkdir(mode=0o700, parents=True)
            payload = service / 'service-data'
            payload.write_bytes(b'\x00ROUGAROU_LOCAL_RECOVERY_BYTES\xff\n')
            original = payload.read_bytes()
            password = home / 'backup-password'
            password.write_text('disposable-backup-password-never-publish\n')
            password.chmod(0o600)
            url = home / 'webhook-url'
            url.write_text(f'http://127.0.0.1:{server.server_port}/private-endpoint\n')
            url.chmod(0o600)
            token = home / 'webhook-token'
            token.write_text('private-webhook-acceptance-token\n')
            token.chmod(0o600)
            command('notify', 'configure', '--url-file', str(url), '--token-file', str(token), '--format', 'json', env=env)
            assert not received, 'Configuration unexpectedly delivered a webhook'
            command('notify', 'test', env=env)
            assert json.loads(received[0][1])['event'] == 'test'
            failed = command('jobs', 'run', '--workspace', str(service), '--timeout', '5', '--',
                             '/usr/bin/python3', '-c', 'print("PRIVATE_JOB_LOG"); raise SystemExit(7)', env=env).stdout.strip()
            queued = command('jobs', 'run', '--workspace', str(service), '--delay', '3600', '--', '/usr/bin/true', env=env).stdout.strip()
            worker = subprocess.Popen([CLI, 'worker'], env=env, stdin=subprocess.DEVNULL,
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                record = parsed('jobs', 'show', failed, '--json', env=env)
                if record['state'] == 'failed':
                    break
                time.sleep(.1)
            else:
                raise AssertionError('Isolated worker did not finish the explicit local test command')
            worker.terminate()
            assert worker.wait(timeout=5) == 0
            worker = None
            command('notify', 'check', env=env)
            command('notify', 'check', env=env)
            events = [json.loads(body) for _, body in received]
            assert [row['job_id'] for row in events if row['event'] == 'job_unsuccessful'] == [failed]
            for headers, body in received:
                assert headers.get('Authorization') == 'Bearer private-webhook-acceptance-token'
                for private in (b'PRIVATE_JOB_LOG', b'private-provider-acceptance', b'private-webhook-acceptance-token',
                                b'private-workspace', b'private-endpoint'):
                    assert private not in body, 'Webhook payload included private fixture content'
            command('backup', 'init', '--repository', str(root / 'encrypted-repository'),
                    '--password-file', str(password), '--include', str(service), env=env)
            created = parsed('backup', 'create', '--json', env=env)
            identifier = created['snapshot']
            assert len(identifier) == 64 and created['worker_stopped'] is False
            assert parsed('backup', 'list', '--json', env=env)[0]['id'] == identifier
            assert parsed('backup', 'check', '--read-data', '--json', env=env)['all_data_read'] is True
            target = home / 'Rougarou-Recovery/reviewed'
            preview = parsed('backup', 'restore', identifier, '--target', str(target), '--json', env=env)
            assert preview['applied'] is False and not target.exists()
            restored = parsed('backup', 'restore', identifier, '--target', str(target), '--apply', '--json', env=env)
            assert restored['applied'] is True and restored['quarantined_jobs'] == 1
            assert restored['services_started'] is False
            assert (target / str(payload).lstrip('/')).read_bytes() == original
            assert (target / 'rougarou-backup-capsule/config/credentials.json').read_bytes() == credential.read_bytes()
            assert (target / 'rougarou-backup-capsule/state/logs' / (failed + '.log')).read_bytes() == (state / 'logs' / (failed + '.log')).read_bytes()
            with sqlite3.connect(target / 'prepared/rougarou-state/jobs.sqlite3') as database:
                recovered = dict(database.execute('SELECT id,state FROM jobs'))
                assert recovered == {failed: 'failed', queued: 'interrupted'}
                assert database.execute('SELECT COUNT(*) FROM worker').fetchone()[0] == 0
            assert parsed('jobs', 'show', queued, '--json', env=env)['state'] == 'queued'
            assert payload.read_bytes() == original
            existing = command('backup', 'restore', identifier, '--target', str(target), '--apply', env=env, allowed=(1,))
            assert 'new directory' in existing.stderr
            assert (target / 'RECOVERY.json').stat().st_mode & 0o777 == 0o600
            for filename in ('backup.json', 'notifications.json'):
                assert (settings / filename).stat().st_mode & 0o777 == 0o600
            menu_cancel(env, b'0\n')
            menu_cancel(env, b'\x03')
            menu_cancel(env, b'\x04')
            command('menu', env=env, allowed=(1,))  # Pipes are not a menu input mechanism.
            return {'encrypted_backup_roundtrip': True, 'restore_preview_then_isolated_apply': True,
                    'restored_pending_job_quarantined': True, 'source_queue_unchanged': True,
                    'webhook_loopback_delivery_and_job_dedup': True, 'webhook_payload_private': True,
                    'installed_menu_pty_cancel': True, 'provider_requests': 0, 'external_webhooks': 0}
    finally:
        if worker is not None and worker.poll() is None:
            worker.terminate()
            worker.wait(timeout=5)
        server.shutdown()
        server.server_close()
        listener.join(timeout=2)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--skip-installed-host-checks', action='store_true', help='Disposable container smoke only; never use for release VM acceptance')
    options = parser.parse_args()
    assert os.geteuid() != 0, 'Run as the disposable guest operator, not root'
    began = time.monotonic()
    actual_config = None if options.skip_installed_host_checks else installed_host_checks()
    package_before = file_hash('/var/lib/dpkg/status')
    update_pointer = Path('/var/lib/rougarou/updates/current.json')
    assert not update_pointer.exists(), 'Fresh guest must not already contain guided update metadata'
    help_output = command('help').stdout
    assert all(word in help_output for word in ('menu', 'update', 'backup', 'notify'))
    recovery = json.loads(command('update', 'recovery', '--json').stdout)
    assert recovery['automatic_rollback'] is False and recovery['steps']
    plan = command('update', 'plan', '--json', allowed=(1,))
    assert 'refresh' in plan.stderr and not update_pointer.exists()
    assert file_hash('/var/lib/dpkg/status') == package_before
    result = isolated_operator_roundtrip()
    assert file_hash('/var/lib/dpkg/status') == package_before, 'Operator feature test changed package state'
    if actual_config is not None:
        assert not (actual_config / 'notifications.json').exists()
        assert not (actual_config / 'backup.json').exists()
        installed_host_checks()
    result.update(result='PASS', installed_host_checks=not options.skip_installed_host_checks,
                  fresh_update_plan_refused_without_refresh=True, package_state_unchanged=True,
                  elapsed_seconds=round(time.monotonic() - began, 2))
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    main()
