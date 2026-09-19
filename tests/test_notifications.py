"""Local-only webhook integration tests with real queue SQLite and HTTP capture."""
import contextlib
import copy
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
import unittest
from unittest.mock import patch

LIB = Path(os.environ.get('ROUGAROU_TEST_LIB', Path(__file__).resolve().parents[1] / 'rootfs/usr/lib/rougarou'))
sys.path.insert(0, str(LIB))
if (Path(__file__).parent / 'notifications.py').exists():
    sys.path.insert(0, str(Path(__file__).parent))
import cli
import jobs
import notifications


class Capture:
    """A real loopback endpoint. Never proxies or contacts another server."""
    def __init__(self):
        self.requests = []
        self.responses = deque()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers.get('Content-Length', 0)))
                owner.requests.append({'path': self.path, 'headers': dict(self.headers), 'body': body})
                status, headers = owner.responses.popleft() if owner.responses else (204, {})
                if status is None:
                    self.connection.sendall(b'PRIVATE_RESPONSE_MARKER\r\n\r\n')
                    self.close_connection = True
                    return
                self.send_response(status)
                for key, value in headers.items():
                    self.send_header(key, value)
                self.send_header('Content-Length', '0')
                self.end_headers()

            def log_message(self, *_):
                pass

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': .02}, daemon=True)
        self.thread.start()
        self.url = f'http://127.0.0.1:{self.server.server_port}/webhook?private=query-secret'

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)


class NotificationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.env = patch.dict(os.environ, {'HOME': str(self.root), 'XDG_CONFIG_HOME': str(self.root / 'config'),
                                         'XDG_STATE_HOME': str(self.root / 'state')})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.endpoint = Capture()
        self.addCleanup(self.endpoint.close)
        self.health = patch.object(notifications, 'health_events', return_value={})
        self.health_mock = self.health.start()
        self.addCleanup(self.health.stop)
        self.output = io.StringIO()

    def configure(self, kind='json'):
        url = self.root / 'endpoint'
        token = self.root / 'token'
        url.write_text(self.endpoint.url + '\n')
        token.write_text('private-bearer-token\n')
        url.chmod(0o600)
        token.chmod(0o600)
        with contextlib.redirect_stdout(self.output), patch.object(cli, 'require_operator'):
            self.assertEqual(notifications.main(['configure', '--url-file', str(url), '--token-file', str(token),
                                                 '--format', kind]), 0)
        return notifications.load_config()

    def failure(self, state='failed'):
        with jobs.database() as db:
            job = jobs.submit(db, 'ai', {'prompt': 'PRIVATE_PROMPT', 'argv': ['PRIVATE_ARGV']}, self.root)
            jobs.finish(db, job, state, reason='PRIVATE_REASON', exit_code=9)
        path = jobs.log_path(job)
        path.parent.mkdir(mode=0o700, exist_ok=True)
        path.write_text('PRIVATE_LOG\n')
        path.chmod(0o600)
        return job

    def state(self):
        return cli.read_json(jobs.state_dir() / notifications.STATE_NAME)

    def test_all_formats_deliver_native_http_and_only_intended_fields(self):
        event = {'type': 'job_unsuccessful', 'job_id': '1234567890abcdef', 'state': 'failed',
                 'message': 'Job 1234567890abcdef finished failed.'}
        for kind in notifications.FORMATS:
            with self.subTest(format=kind):
                config = self.configure(kind)
                with patch.object(notifications.socket, 'gethostname', return_value='test-host'):
                    notifications.send(config, event)
                request = self.endpoint.requests[-1]
                self.assertEqual(request['headers']['Authorization'], 'Bearer private-bearer-token')
                self.assertNotIn(b'private-bearer-token', request['body'])
                self.assertNotIn(b'query-secret', request['body'])
                if kind == 'ntfy':
                    self.assertEqual(request['headers']['Content-Type'], 'text/plain; charset=utf-8')
                    self.assertEqual(request['headers']['Title'], 'Rougarou OS')
                    self.assertEqual(request['body'].decode(), 'Rougarou / test-host: ' + event['message'])
                else:
                    data = json.loads(request['body'])
                    if kind == 'json':
                        self.assertEqual(data, {'schema': 1, 'host': 'test-host', 'event': event['type'],
                                               'message': event['message'], 'job_id': event['job_id'], 'state': 'failed'})
                    elif kind == 'discord':
                        self.assertEqual(data, {'content': 'Rougarou / test-host: ' + event['message'],
                                               'allowed_mentions': {'parse': []}})
                    else:
                        self.assertEqual(data, {'text': 'Rougarou / test-host: ' + event['message'], 'mrkdwn': False})
        self.assertEqual(len(self.endpoint.requests), 4)

    def test_redirects_are_refused_and_token_never_reaches_target(self):
        target = Capture()
        self.addCleanup(target.close)
        config = self.configure()
        for code in (301, 302, 303, 307, 308):
            self.endpoint.responses.append((code, {'Location': target.url}))
            with self.subTest(code=code), self.assertRaises(cli.OperatorError) as error:
                notifications.send(config, {'type': 'test', 'message': 'test'})
            self.assertNotIn('query-secret', str(error.exception))
            self.assertNotIn('private-bearer-token', str(error.exception))
        self.assertEqual(target.requests, [])

    def test_ambient_proxy_environment_does_not_receive_webhook(self):
        proxy = Capture()
        self.addCleanup(proxy.close)
        config = self.configure()
        env = {name: proxy.url for name in ('http_proxy', 'HTTP_PROXY', 'https_proxy', 'HTTPS_PROXY', 'all_proxy', 'ALL_PROXY')}
        env.update(no_proxy='', NO_PROXY='')
        with patch.dict(os.environ, env):
            notifications.send(config, {'type': 'test', 'message': 'test'})
        self.assertEqual(len(self.endpoint.requests), 1)
        self.assertEqual(proxy.requests, [])

    def test_malformed_http_response_is_private_and_cursor_stays_pending(self):
        self.configure()
        self.failure()
        before = self.state()
        self.endpoint.responses.append((None, {}))
        with self.assertRaises(cli.OperatorError) as error:
            notifications.check()
        self.assertEqual(self.state(), before)
        for secret in ('PRIVATE_RESPONSE_MARKER', 'private-bearer-token', 'query-secret'):
            self.assertNotIn(secret, str(error.exception))
        self.assertEqual(notifications.check(), 1)

    def test_configuration_committed_at_lock_boundary_never_uses_old_endpoint(self):
        current = Capture()
        self.addCleanup(current.close)
        config = self.configure()
        self.health_mock.return_value = {'disk': {'type': 'low_disk', 'message': 'Disk needs attention.'}}
        original_lock = notifications.delivery_lock

        @contextlib.contextmanager
        def configure_before_lock_yields():
            with original_lock():
                cli.atomic_write(cli.config_dir() / notifications.CONFIG_NAME,
                                 json.dumps(dict(config, url=current.url)), backup=False)
                notifications.save_state({'schema': 1, 'queue': notifications.queue_position(), 'health': []})
                yield

        with patch.object(notifications, 'delivery_lock', configure_before_lock_yields):
            self.assertEqual(notifications.check(), 1)
        self.assertEqual(self.endpoint.requests, [])
        self.assertEqual(len(current.requests), 1)

    def test_failure_keeps_cursor_retry_deduplicates_and_new_failure_delivers(self):
        self.configure()
        before = self.state()
        first = self.failure()
        self.endpoint.responses.append((503, {}))
        with self.assertRaises(cli.OperatorError):
            notifications.check()
        self.assertEqual(self.state(), before)
        self.assertEqual(notifications.check(), 1)
        accepted = self.state()
        self.assertEqual(notifications.check(), 0)
        self.assertEqual(self.state(), accepted)
        second = self.failure('timed_out')
        self.assertEqual(notifications.check(), 1)
        data = [json.loads(request['body']) for request in self.endpoint.requests]
        self.assertEqual([row['job_id'] for row in data], [first, first, second])
        self.assertEqual(data[-1]['state'], 'timed_out')
        self.assertEqual(notifications.check(), 0)

    def test_configure_skips_existing_history_but_preserves_it_and_private_files(self):
        old = self.failure()
        before = (jobs.state_dir() / 'jobs.sqlite3').read_bytes()
        self.configure()
        self.assertEqual(notifications.check(), 0)
        self.assertEqual(self.endpoint.requests, [])
        with jobs.read_database() as db:
            self.assertEqual(jobs.get_job(db, old)['state'], 'failed')
        self.assertEqual((jobs.state_dir() / 'jobs.sqlite3').read_bytes(), before)
        self.assertIn('PRIVATE_LOG', jobs.log_path(old).read_text())
        for path in (cli.config_dir() / notifications.CONFIG_NAME, jobs.state_dir() / notifications.STATE_NAME):
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)
        self.assertNotIn('query-secret', self.output.getvalue())
        self.assertNotIn('private-bearer-token', self.output.getvalue())
        new = self.failure('interrupted')
        self.assertEqual(notifications.check(), 1)
        self.assertEqual(json.loads(self.endpoint.requests[-1]['body'])['job_id'], new)

    def test_partly_delivered_batch_retries_only_the_unaccepted_event(self):
        self.configure()
        first, second = self.failure(), self.failure('interrupted')
        self.endpoint.responses.extend([(204, {}), (503, {})])
        with self.assertRaises(cli.OperatorError):
            notifications.check()
        cursor = self.state()['queue']['sequence']
        with jobs.read_database() as db:
            delivered = db.execute('SELECT job_id FROM events WHERE seq=?', (cursor,)).fetchone()[0]
        self.assertEqual(delivered, first)
        self.assertEqual(notifications.check(), 1)
        self.assertEqual(notifications.check(), 0)
        self.assertEqual([json.loads(r['body'])['job_id'] for r in self.endpoint.requests], [first, second, second])

    def test_event_payload_excludes_prompts_logs_argv_paths_and_reason(self):
        self.configure()
        job = self.failure()
        self.assertEqual(notifications.check(), 1)
        data = json.loads(self.endpoint.requests[0]['body'])
        self.assertEqual(set(data), {'schema', 'host', 'event', 'message', 'job_id', 'state'})
        self.assertEqual(data['job_id'], job)
        body = self.endpoint.requests[0]['body'].decode()
        for secret in ('PRIVATE_PROMPT', 'PRIVATE_ARGV', 'PRIVATE_REASON', 'PRIVATE_LOG', str(self.root),
                       'private-bearer-token', 'query-secret'):
            self.assertNotIn(secret, body)

    def test_twenty_event_batch_limit_eventually_delivers_once(self):
        self.configure()
        expected = [self.failure() for _ in range(23)]
        self.assertEqual(notifications.check(), 20)
        self.assertEqual(notifications.check(), 3)
        self.assertEqual(notifications.check(), 0)
        self.assertEqual([json.loads(r['body'])['job_id'] for r in self.endpoint.requests], expected)

    def test_replaced_database_starts_from_now_without_replaying(self):
        self.configure()
        self.failure()
        self.assertEqual(notifications.check(), 1)
        for suffix in ('', '-wal', '-shm'):
            (jobs.state_dir() / ('jobs.sqlite3' + suffix)).unlink(missing_ok=True)
        self.failure()
        self.assertEqual(notifications.check(), 0)
        new = self.failure()
        self.assertEqual(notifications.check(), 1)
        self.assertEqual(json.loads(self.endpoint.requests[-1]['body'])['job_id'], new)

    def test_health_alert_dedup_resolution_rearms_and_failed_health_retries(self):
        self.configure()
        disk = {'disk': {'type': 'low_disk', 'message': 'Disk needs attention.'}}
        self.health_mock.return_value = disk
        self.endpoint.responses.append((500, {}))
        with self.assertRaises(cli.OperatorError):
            notifications.check()
        self.assertEqual(self.state()['health'], [])
        self.assertEqual(notifications.check(), 1)
        self.assertEqual(notifications.check(), 0)
        self.health_mock.return_value = {}
        self.assertEqual(notifications.check(), 0)
        self.assertEqual(self.state()['health'], [])
        self.health_mock.return_value = disk
        self.assertEqual(notifications.check(), 1)
        self.assertEqual(len(self.endpoint.requests), 3)

    def test_real_health_mapping_uses_fixed_text_not_repository_private_data(self):
        self.health.stop()
        updates = SimpleNamespace(
            repository_status=lambda: {'sources': [
                {'name': 'PRIVATE_REPO', 'status': state, 'valid_until': 'expiry', 'uri': 'PRIVATE_URL'}
                for state in ('valid', 'expiring', 'expired', 'missing', 'invalid', 'policy_error', 'unavailable')]},
            reboot_status=lambda: {'recommended': True, 'required': False, 'boot_id': 'current-boot'})
        with patch.dict(sys.modules, {'updates': updates}), patch.object(notifications.shutil, 'disk_usage',
                return_value=SimpleNamespace(total=20 * 1024**3, free=500 * 1024**2)):
            events = notifications.health_events()
        self.assertEqual({e['type'] for e in events.values()}, {'low_disk', 'reboot_recommended',
            'repository_expiring', 'repository_expired', 'repository_missing', 'repository_invalid',
            'repository_policy_error', 'repository_unavailable'})
        self.configure()
        for event in events.values():
            notifications.send(notifications.load_config(), event)
        for request in self.endpoint.requests:
            self.assertNotIn(b'PRIVATE_REPO', request['body'])
            self.assertNotIn(b'PRIVATE_URL', request['body'])

    def test_malformed_state_fails_before_delivery_and_retains_file(self):
        self.configure()
        valid = self.state()
        invalid = []
        for value in (-1, True, '3', None):
            row = copy.deepcopy(valid)
            row['queue']['sequence'] = value
            invalid.append(row)
        for value in ('not-an-id', 3, [], 'g' * 16):
            row = copy.deepcopy(valid)
            row['queue']['identity'] = value
            invalid.append(row)
        for value in ({}, [None], ['bad\nkey'], ['a'] * 1025):
            row = copy.deepcopy(valid)
            row['health'] = value
            invalid.append(row)
        invalid.extend([{'schema': 1}, {**valid, 'extra': 'field'}, {**valid, 'schema': 2}])
        path = jobs.state_dir() / notifications.STATE_NAME
        for row in invalid:
            with self.subTest(state=row):
                notifications.save_state(row)
                before = path.read_bytes()
                with self.assertRaises(cli.OperatorError):
                    notifications.check()
                self.assertEqual(path.read_bytes(), before)
        self.assertEqual(self.endpoint.requests, [])

    def test_private_secret_input_and_endpoint_validation(self):
        secret = self.root / 'unsafe'
        secret.write_text(self.endpoint.url)
        secret.chmod(0o644)
        with self.assertRaises(cli.OperatorError):
            notifications.secret_file(secret)
        secret.chmod(0o600)
        linked = self.root / 'linked'
        linked.symlink_to(secret)
        with self.assertRaises(cli.OperatorError):
            notifications.secret_file(linked)
        for url in ('http://example.com/hook', 'https://user:pass@example.com/hook',
                    'https://example.com/#fragment', 'https://example.com/a\nheader', 'file:///tmp/test'):
            with self.subTest(url=url), self.assertRaises(cli.OperatorError):
                notifications.validate_url(url)

    def test_delivery_lock_excludes_overlapping_checks(self):
        self.configure()
        with notifications.delivery_lock(), self.assertRaises(cli.OperatorError):
            notifications.check()
        self.assertEqual(self.endpoint.requests, [])
        self.assertEqual(notifications.check(), 0)

    def test_user_service_failures_are_bounded_and_no_secret_is_printed(self):
        self.configure()
        with patch.object(notifications.subprocess, 'run', side_effect=subprocess.TimeoutExpired('systemctl', 10)) as run:
            with self.assertRaisesRegex(cli.OperatorError, 'timed out'):
                notifications.systemctl('is-enabled', notifications.UNIT, capture=True)
            self.assertEqual(run.call_args.kwargs['timeout'], 10)
        with patch.object(cli, 'require_operator'), patch.object(notifications.subprocess, 'run',
                return_value=SimpleNamespace(returncode=1)) as run:
            with self.assertRaisesRegex(cli.OperatorError, 'reload'):
                notifications.main(['enable'])
            self.assertEqual(run.call_count, 1)
        self.assertEqual(self.endpoint.requests, [])

    def test_disable_stops_timer_and_active_check_before_reporting_success(self):
        states = {notifications.UNIT: 'active', notifications.SERVICE: 'active'}
        def manager(*arguments, **options):
            if arguments == ('disable', '--now', notifications.UNIT):
                states[notifications.UNIT] = 'inactive'
            elif arguments == ('stop', notifications.SERVICE):
                self.assertEqual(states[notifications.UNIT], 'inactive')
                states[notifications.SERVICE] = 'inactive'
            else:
                self.fail('Unexpected service-manager operation')
            return SimpleNamespace(returncode=0)
        with patch.object(cli, 'require_operator'), patch.object(notifications, 'systemctl', side_effect=manager), \
                contextlib.redirect_stdout(self.output):
            self.assertEqual(notifications.main(['disable']), 0)
        self.assertEqual(set(states.values()), {'inactive'})
        with patch.object(cli, 'require_operator'), patch.object(notifications, 'systemctl',
                side_effect=[SimpleNamespace(returncode=0), SimpleNamespace(returncode=1)]), \
                self.assertRaisesRegex(cli.OperatorError, 'could not be stopped'):
            notifications.main(['disable'])


if __name__ == '__main__':
    unittest.main()
