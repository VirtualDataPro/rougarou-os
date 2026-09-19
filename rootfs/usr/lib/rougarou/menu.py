"""Small terminal control menu over the same public operator commands."""
from __future__ import annotations

from pathlib import Path
import subprocess
import sys

import cli


def choose(title, entries):
    print('\n' + title)
    for index, (label, _) in enumerate(entries, 1):
        print(f'  {index}  {label}')
    print('  0  Back to shell' if title == 'ROUGAROU OS' else '  0  Back')
    answer = cli.field('Choose', '0')
    if answer == '0':
        return None
    if not answer.isdecimal() or not 1 <= int(answer) <= len(entries):
        print('Choose one of the listed numbers.')
        return False
    return entries[int(answer) - 1][1]


def command(*argv):
    # A child preserves the menu when an agent replaces itself with exec().
    # Every argument stays an argv element, including user-entered paths/IDs.
    entrypoint = Path(__file__).resolve().parents[2] / 'local/bin/rougarou'
    result = subprocess.run([sys.executable, str(entrypoint), *argv])
    if result.returncode:
        print(f'Command exited with status {result.returncode}. Your shell and menu remain available.')
    return result.returncode


def job_action(action):
    if action == 'list':
        command('jobs', 'list')
    elif action == 'ack-all':
        if cli.confirm('Acknowledge all currently unsuccessful runs? Logs and history will be retained.', False):
            command('jobs', 'ack', '--all')
    else:
        job_id = cli.field('Job ID (blank to cancel)')
        if job_id:
            command('jobs', action, job_id)


def jobs_menu():
    entries = [('List jobs', 'list'), ('Inspect one job', 'show'), ('Read job logs', 'logs'),
               ('Acknowledge one unsuccessful job', 'ack'), ('Acknowledge all unsuccessful jobs', 'ack-all')]
    while True:
        action = choose('Managed jobs', entries)
        if action is None:
            return
        if action is not False:
            job_action(action)


def updates_menu():
    entries = [('Review planned updates', 'plan'), ('Refresh signed package metadata', 'refresh'),
               ('Apply a reviewed plan', 'apply'), ('Latest update transaction', 'status'),
               ('Recovery instructions', 'recovery')]
    while True:
        action = choose('Updates and recovery', entries)
        if action is None:
            return
        if action is False:
            continue
        if action == 'apply':
            plan = cli.field('Reviewed plan ID (blank to cancel)')
            if not plan:
                continue
            recovery = cli.field('Recovery point reference (for example, a Proxmox snapshot name)')
            if recovery and cli.confirm('Apply this plan using that recovery point?', False):
                command('update', 'apply', '--plan', plan, '--recovery-point', recovery, '--confirm')
        else:
            command('update', action)


def backups_menu():
    entries = [('Configure an encrypted backup repository', 'init'), ('Create a backup', 'create'),
               ('List backups', 'list'), ('Verify backup data', 'check'), ('Preview or stage a restore', 'restore')]
    while True:
        action = choose('Backups and restores', entries)
        if action is None:
            return
        if action is False:
            continue
        if action == 'init':
            repository = cli.field('Repository directory (blank to cancel)')
            if not repository:
                continue
            password = cli.field('Existing private password file')
            if password:
                argv = ['backup', 'init', '--repository', repository, '--password-file', password]
                if cli.confirm('Attach to an existing repository?', False):
                    argv.append('--existing')
                include = cli.field('Optional additional service-data directory (blank to skip)')
                if include:
                    argv += ['--include', include]
                command(*argv)
        elif action == 'restore':
            snapshot = cli.field('Full 64-character snapshot ID (blank to cancel)')
            if not snapshot:
                continue
            if len(snapshot) != 64 or any(c not in '0123456789abcdef' for c in snapshot):
                print('Copy the full snapshot ID from rougarou backup list.')
                continue
            if command('backup', 'restore', snapshot):
                continue
            if cli.confirm('Restore into a new recovery directory for inspection?', False):
                target = cli.field('New recovery directory (blank for the default)')
                argv = ['backup', 'restore', snapshot, '--apply']
                if target:
                    argv += ['--target', target]
                command(*argv)
        else:
            command('backup', action, *(['--read-data'] if action == 'check' else []))


def notifications_menu():
    entries = [('Notification status', 'status'), ('Configure a webhook', 'configure'),
               ('Send a test notification', 'test'), ('Enable notifications', 'enable'),
               ('Disable notifications', 'disable')]
    while True:
        action = choose('Optional notifications', entries)
        if action is None:
            return
        if action is False:
            continue
        if action == 'configure':
            kind = cli.field('Format: json, discord, slack or ntfy', 'json')
            if kind not in {'json', 'discord', 'slack', 'ntfy'}:
                print('Choose a supported format.')
                continue
            token = cli.field('Optional private bearer-token file (blank to skip)')
            command('notify', 'configure', '--format', kind, *(['--token-file', token] if token else []))
        else:
            command('notify', action)


def containers_menu():
    entries = [('List Docker containers', ['docker', 'ps', '-a']),
               ('Set up rootless Docker for this account', ['/usr/local/bin/rougarou-docker', 'setup']),
               ('List Podman containers', ['podman', 'ps', '-a'])]
    while True:
        action = choose('Containers', entries)
        if action is None:
            return
        if action is not False:
            try:
                subprocess.run(action, check=False)
            except FileNotFoundError:
                print('That container runtime is not installed. See rougarou help and the container guide.')


def main(argv):
    if argv:
        raise cli.OperatorError('Usage: rougarou menu')
    cli.require_operator()
    cli.require_terminal()
    actions = [('Start your agent', lambda: command('ai')),
               ('Choose or connect an agent', lambda: command('provider')),
               ('Managed jobs', jobs_menu), ('Containers', containers_menu),
               ('Updates and recovery', updates_menu), ('Backups and restores', backups_menu),
               ('Notifications', notifications_menu), ('Check this host', lambda: command('doctor')),
               ('Accounts and setup', lambda: command('setup'))]
    while True:
        action = choose('ROUGAROU OS', actions)
        if action is None:
            return 0
        if action is not False:
            action()
