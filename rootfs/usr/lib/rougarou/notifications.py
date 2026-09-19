"""Opt-in, bounded webhook delivery. Never runs as part of login."""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import getpass
import http.client
import ipaddress
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import urllib.error
import urllib.request
from urllib.parse import urlsplit

import cli
import jobs

UNIT = 'rougarou-notify.timer'
SERVICE = 'rougarou-notify.service'
FORMATS = ('json', 'discord', 'slack', 'ntfy')
CONFIG_NAME = 'notifications.json'
STATE_NAME = 'notification-delivery.json'


def validate_url(value):
    if not isinstance(value, str) or not value or len(value) > 4096 or any(ord(c) < 33 or ord(c) == 127 for c in value):
        raise cli.OperatorError('Invalid webhook URL.')
    try:
        parsed = urlsplit(value)
        _ = parsed.port
        local = parsed.hostname == 'localhost'
        try:
            local = local or ipaddress.ip_address(parsed.hostname or '').is_loopback
        except ValueError:
            pass
        if not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
            raise ValueError
        if parsed.scheme != 'https' and not (parsed.scheme == 'http' and local):
            raise ValueError
    except ValueError:
        raise cli.OperatorError('Use an HTTPS webhook URL; HTTP is supported only on loopback.') from None
    return value


def load_config():
    config = cli.read_json(cli.config_dir() / CONFIG_NAME)
    if not config:
        raise cli.OperatorError('Notifications are not configured. Run rougarou notify configure.')
    if set(config) != {'schema', 'url', 'format', 'token'} or config['schema'] != 1 or config['format'] not in FORMATS:
        raise cli.OperatorError('Invalid notification configuration; run rougarou notify configure.')
    validate_url(config['url'])
    token = config['token']
    if not isinstance(token, str) or len(token) > 4096 or any(ord(c) < 33 or ord(c) == 127 for c in token):
        raise cli.OperatorError('Invalid webhook bearer token.')
    return config


def secret_file(path):
    path = Path(path).expanduser()
    cli.check_regular(path, private=True)
    if not path.is_file() or path.stat().st_size > 4096:
        raise cli.OperatorError('Secret input must be an existing private file of at most 4096 bytes.')
    return path.read_text().strip()


def queue_position():
    if not (jobs.state_dir() / 'jobs.sqlite3').exists():
        return {'identity': None, 'sequence': 0}
    with jobs.read_database() as db:
        db.execute('BEGIN')
        first = db.execute('SELECT job_id FROM events ORDER BY seq LIMIT 1').fetchone()
        sequence = db.execute('SELECT COALESCE(MAX(seq),0) FROM events').fetchone()[0]
    return {'identity': first[0] if first else None, 'sequence': sequence}


def save_state(state):
    cli.atomic_write(jobs.state_dir() / STATE_NAME, json.dumps(state, sort_keys=True) + '\n', backup=False)


@contextlib.contextmanager
def delivery_lock():
    root = jobs.state_dir()
    cli.private_directory(root)
    path = root / 'notification-delivery.lock'
    cli.check_regular(path, private=True)
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise cli.OperatorError('A notification check is already running.') from None
        yield
    finally:
        os.close(fd)


class NoRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


def send(config, event):
    # Only fixed health summaries and job IDs are supplied here. No prompt,
    # workspace, command, log output, credentials or source URLs leave the host.
    host = ''.join(c for c in socket.gethostname()[:100] if c.isprintable())
    message = f'Rougarou / {host}: {event["message"]}'
    headers = {'Content-Type': 'application/json', 'User-Agent': 'Rougarou-Notify/1'}
    if config['format'] == 'discord':
        payload = {'content': message, 'allowed_mentions': {'parse': []}}
    elif config['format'] == 'slack':
        payload = {'text': message, 'mrkdwn': False}
    elif config['format'] == 'ntfy':
        payload = None
        headers['Content-Type'] = 'text/plain; charset=utf-8'
        headers['Title'] = 'Rougarou OS'
    else:
        payload = {'schema': 1, 'host': host, 'event': event['type'], 'message': event['message']}
        if 'job_id' in event:
            payload['job_id'] = event['job_id']
            payload['state'] = event['state']
    body = message.encode() if payload is None else json.dumps(payload).encode()
    if config['token']:
        headers['Authorization'] = 'Bearer ' + config['token']
    request = urllib.request.Request(config['url'], data=body, headers=headers, method='POST')
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirects)
        with opener.open(request, timeout=5) as response:
            if not 200 <= response.status < 300:
                raise cli.OperatorError('Webhook rejected the notification; it will be retried.')
    except (OSError, urllib.error.URLError, http.client.HTTPException, ValueError) as error:
        # HTTP errors may include the private URL or server response. Never
        # print or chain them into a user journal or terminal traceback.
        if isinstance(error, urllib.error.HTTPError):
            error.close()
        raise cli.OperatorError('Webhook delivery failed; check the endpoint, token and connectivity. Unsent events will be retried.') from None


def health_events():
    events = {}
    usage = shutil.disk_usage(Path.home())
    if usage.free < 1024**3 or usage.free / usage.total < .1:
        events['disk'] = {'type': 'low_disk', 'message': 'Operator filesystem has less than 1 GiB or 10% free space.'}
    from updates import repository_status, reboot_status
    reboot = reboot_status()
    if reboot['recommended']:
        events['reboot:' + reboot['boot_id']] = {
            'type': 'reboot_required' if reboot['required'] else 'reboot_recommended',
            'message': ('Installed packages request a reboot.' if reboot['required'] else
                        'A reboot is recommended after core package updates.') + ' Schedule it when workloads can stop.'}
    report = repository_status()
    for source in report['sources']:
        status = source['status']
        if status in {'expiring', 'expired', 'missing', 'invalid', 'policy_error', 'unavailable'}:
            name = source['name']
            key = 'repository:' + name + ':' + status + ':' + str(source.get('valid_until'))
            messages = {
                'expiring': 'Signed repository metadata expires within seven days.',
                'expired': 'Signed repository metadata has expired; refresh or renew the channel.',
                'missing': 'No verified cached metadata is available for a configured repository.',
                'invalid': 'Repository metadata could not be authenticated.',
                'policy_error': 'Package source policy needs operator review.',
                'unavailable': 'Local repository status is temporarily unavailable.',
            }
            events[key] = {'type': 'repository_' + status, 'message': messages[status] + ' Run rougarou update plan.'}
    return events


def validate_state(state):
    if set(state) != {'schema', 'queue', 'health'} or state['schema'] != 1:
        raise cli.OperatorError('Invalid notification state; reconfigure notifications to start from now.')
    queue = state['queue']
    if not isinstance(queue, dict) or set(queue) != {'identity', 'sequence'}:
        raise cli.OperatorError('Invalid notification queue cursor.')
    identity = queue['identity']
    if identity is not None and (not isinstance(identity, str) or len(identity) != 16 or any(c not in '0123456789abcdef' for c in identity)):
        raise cli.OperatorError('Invalid notification queue identity.')
    if type(queue['sequence']) is not int or queue['sequence'] < 0:
        raise cli.OperatorError('Invalid notification queue cursor.')
    if not isinstance(state['health'], list) or len(state['health']) > 1024 or any(
            not isinstance(key, str) or len(key) > 1024 or not key.isprintable() for key in state['health']):
        raise cli.OperatorError('Invalid notification health state.')


def check():
    sent = 0
    with delivery_lock():
        config = load_config()
        state = cli.read_json(jobs.state_dir() / STATE_NAME)
        if not state:
            state = {'schema': 1, 'queue': queue_position(), 'health': []}
            save_state(state)
        validate_state(state)
        position = queue_position()
        previous = state['queue']
        # A replaced/restored queue should not replay its entire old history.
        if previous.get('identity') not in (None, position['identity']) or previous.get('sequence', 0) > position['sequence']:
            state['queue'] = position
            save_state(state)
        elif position['sequence'] > previous.get('sequence', 0):
            with jobs.read_database() as db:
                rows = db.execute("""SELECT seq,job_id,state FROM events
                    WHERE seq>? AND seq<=? AND state IN ('failed','timed_out','interrupted')
                    ORDER BY seq LIMIT 20""", (previous.get('sequence', 0), position['sequence'])).fetchall()
            for row in rows:
                send(config, {'type': 'job_unsuccessful', 'job_id': row['job_id'], 'state': row['state'],
                              'message': f'Job {row["job_id"]} finished {row["state"]}; inspect rougarou jobs show {row["job_id"]}.'})
                state['queue'] = {'identity': position['identity'], 'sequence': row['seq']}
                save_state(state)
                sent += 1
            if len(rows) < 20:
                state['queue'] = position
                save_state(state)
        current = health_events()
        prior_health = set(state['health'])
        state['health'] = sorted(prior_health.intersection(current))
        save_state(state)
        for key, event in current.items():
            if key not in prior_health:
                send(config, event)
                state['health'].append(key)
                save_state(state)
                sent += 1
    return sent


def systemctl(*arguments, capture=False):
    try:
        return subprocess.run(['systemctl', '--user', *arguments], capture_output=capture, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        raise cli.OperatorError('User service manager unavailable or timed out.') from None


def main(argv):
    cli.require_operator()
    parser = argparse.ArgumentParser(prog='rougarou notify', description='Optional notifications; disabled until you configure and enable them.')
    commands = parser.add_subparsers(dest='action', required=True)
    configure = commands.add_parser('configure')
    configure.add_argument('--url-file', type=Path)
    configure.add_argument('--token-file', type=Path)
    configure.add_argument('--format', choices=FORMATS, default='json')
    for action in ('test', 'check', 'status', 'enable', 'disable'):
        commands.add_parser(action)
    args = parser.parse_args(argv)
    if args.action == 'configure':
        if args.url_file:
            url = secret_file(args.url_file)
        else:
            cli.require_terminal()
            url = getpass.getpass('Webhook URL (hidden): ').strip()
        config = {'schema': 1, 'url': validate_url(url), 'format': args.format,
                  'token': secret_file(args.token_file) if args.token_file else ''}
        token = config['token']
        if len(token) > 4096 or any(ord(c) < 33 or ord(c) == 127 for c in token):
            raise cli.OperatorError('Invalid webhook bearer token.')
        with delivery_lock():
            cli.atomic_write(cli.config_dir() / CONFIG_NAME, json.dumps(config) + '\n')
            save_state({'schema': 1, 'queue': queue_position(), 'health': []})
        print('Saved privately. Existing job history is not sent. Run rougarou notify test, then rougarou notify enable when ready.')
    elif args.action == 'status':
        configured = (cli.config_dir() / CONFIG_NAME).exists()
        if configured:
            load_config()
        result = systemctl('is-enabled', UNIT, capture=True)
        print(f'Configured: {"yes" if configured else "no"}; timer: {"enabled" if result.returncode == 0 else "disabled"}')
    elif args.action == 'test':
        send(load_config(), {'type': 'test', 'message': 'Notification test requested by the operator.'})
        print('Test notification delivered.')
    elif args.action == 'check':
        print(f'Notifications delivered: {check()}')
    else:
        if args.action == 'enable':
            load_config()
            if systemctl('daemon-reload').returncode:
                raise cli.OperatorError('User service manager could not reload its units.')
        result = systemctl(args.action, '--now', UNIT)
        if result.returncode:
            raise cli.OperatorError('Notification timer action failed; review your user service manager.')
        if args.action == 'disable' and systemctl('stop', SERVICE).returncode:
            raise cli.OperatorError('Timer disabled, but its active notification check could not be stopped; review your user service manager.')
        print('Notification timer ' + ('enabled.' if args.action == 'enable' else 'disabled.'))
        if args.action == 'enable':
            print('For checks after logout, enable lingering with: loginctl enable-linger "$USER"')
    return 0
