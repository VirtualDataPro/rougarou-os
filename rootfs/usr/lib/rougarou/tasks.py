"""Per-operator schedules: systemd triggers, existing queue executes."""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import cli
import jobs

MARKER = '# Managed by rougarou tasks\n'


@contextlib.contextmanager
def database():
    with jobs.database() as db:
        db.executescript('''
            CREATE TABLE IF NOT EXISTS scheduled_tasks (
                name TEXT PRIMARY KEY, calendar TEXT NOT NULL, catch_up INTEGER NOT NULL,
                kind TEXT NOT NULL, spec TEXT NOT NULL, workspace TEXT NOT NULL,
                timeout INTEGER NOT NULL, enabled INTEGER NOT NULL DEFAULT 0,
                revision INTEGER NOT NULL DEFAULT 1, deleted INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS scheduled_runs (
                job_id TEXT PRIMARY KEY, task_name TEXT NOT NULL, revision INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS scheduled_task_runs ON scheduled_runs(task_name);
            CREATE TABLE IF NOT EXISTS scheduled_events (
                id INTEGER PRIMARY KEY, task_name TEXT NOT NULL, at REAL NOT NULL,
                detail TEXT NOT NULL
            );
        ''')
        yield db


@contextlib.contextmanager
def operation():
    cli.private_directory(jobs.state_dir())
    path = jobs.state_dir() / 'tasks.lock'
    cli.check_regular(path, private=True)
    with path.open('a') as lock:
        path.chmod(0o600)
        deadline = time.monotonic() + 10
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise cli.OperatorError('Another task operation is in progress; retry shortly.') from None
                time.sleep(.05)
        yield


def name_check(name):
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,47}', name):
        raise cli.OperatorError('Task name must be 1–48 lowercase letters, digits or hyphens.')
    return name


def get(db, name, *, deleted=False):
    name_check(name)
    row = db.execute('SELECT * FROM scheduled_tasks WHERE name=?', (name,)).fetchone()
    if row is None or (row['deleted'] and not deleted):
        raise cli.OperatorError(f'Unknown task: {name}')
    return dict(row)


def record(db, name, detail):
    db.execute('INSERT INTO scheduled_events(task_name,at,detail) VALUES(?,?,?)',
               (name, time.time(), detail))


def systemctl(*args, check=True):
    try:
        result = subprocess.run(['systemctl', '--user', *args], capture_output=True,
                                text=True, timeout=15)
    except subprocess.TimeoutExpired:
        raise cli.OperatorError('User service manager timed out; inspect systemctl --user status.') from None
    if check and result.returncode:
        raise cli.OperatorError('User service operation failed: ' + jobs.safe_text(result.stderr.strip())[:500])
    return result


def calendar(expression, timezone):
    cli.clean_text(expression, 'Calendar', 256)
    cli.clean_text(timezone, 'Timezone', 128)
    try:
        ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError):
        raise cli.OperatorError('Use an IANA timezone such as UTC or America/Chicago.') from None
    try:
        result = subprocess.run(['systemd-analyze', 'calendar', '--iterations=1', '--', expression + ' ' + timezone],
                                capture_output=True, text=True, timeout=10,
                                env={**os.environ, 'LC_ALL': 'C'})
    except subprocess.TimeoutExpired:
        raise cli.OperatorError('Calendar validation timed out.') from None
    normalized = next((s.split(':', 1)[1].strip() for s in result.stdout.splitlines()
                       if s.strip().startswith('Normalized form:')), None)
    if result.returncode or not normalized or '%' in normalized or '\\' in normalized:
        raise cli.OperatorError('Invalid calendar expression; try daily or Mon..Fri 09:00.')
    return normalized


def definition(args):
    workspace = Path(args.workspace).expanduser().resolve(strict=True)
    if not workspace.is_dir() or not 1 <= args.timeout <= 604800:
        raise cli.OperatorError('Choose a workspace directory and a timeout of 1–604800 seconds.')
    if args.command is not None:
        if args.model or args.provider != 'current':
            raise cli.OperatorError('Command tasks take provider/model options in their command arguments.')
        command = args.command
        if not command or len(command) > 128:
            raise cli.OperatorError('Supply an executable and its unattended arguments after --command.')
        for item in command:
            cli.clean_text(item, 'Command argument', 16384)
        command[0] = cli.require_command(command[0])
        kind, spec = 'command', {'argv': command}
    else:
        prompt = sys.stdin.read(262145) if args.prompt == '-' else args.prompt
        if not prompt or not prompt.strip() or len(prompt) > 262144 or '\0' in prompt:
            raise cli.OperatorError('Provide a prompt of 1–262144 characters, or - for stdin.')
        provider = {'kind': 'codex'} if args.provider == 'codex' else cli.load_config().get('provider')
        if not provider:
            raise cli.OperatorError('Choose a provider first, or use --provider codex.')
        cli.validate_provider(provider)
        if provider['kind'] == 'command':
            raise cli.OperatorError('Use --command with this provider’s unattended arguments.')
        spec = {'provider': provider, 'prompt': prompt, 'sandbox': args.sandbox}
        if args.model:
            spec['model'] = cli.clean_text(args.model, 'Model', 256)
        kind = 'ai'
    return {'calendar': calendar(args.on, args.timezone), 'catch_up': int(args.catch_up),
            'kind': kind, 'spec': json.dumps(spec), 'workspace': str(workspace), 'timeout': args.timeout}


def save(db, name, value, *, edit=False):
    name_check(name)
    with jobs.transaction(db):
        if edit:
            task = get(db, name)
            if task['enabled']:
                raise cli.OperatorError('Pause this task before editing it.')
            db.execute('''UPDATE scheduled_tasks SET calendar=:calendar,catch_up=:catch_up,
                kind=:kind,spec=:spec,workspace=:workspace,timeout=:timeout,revision=revision+1
                WHERE name=:name''', {**value, 'name': name})
        else:
            if db.execute('SELECT 1 FROM scheduled_tasks WHERE name=?', (name,)).fetchone():
                raise cli.OperatorError('Task name already exists or has retained history; choose another name.')
            db.execute('''INSERT INTO scheduled_tasks(name,calendar,catch_up,kind,spec,workspace,timeout)
                VALUES(:name,:calendar,:catch_up,:kind,:spec,:workspace,:timeout)''', {**value, 'name': name})
        record(db, name, 'Edited; manual test required' if edit else 'Created paused')


def ready(task):
    if systemctl('is-active', '--quiet', jobs.UNIT, check=False).returncode:
        raise cli.OperatorError('Worker is inactive. Run rougarou service start first.')
    if not Path(task['workspace']).is_dir():
        raise cli.OperatorError('Task workspace is unavailable.')
    spec = json.loads(task['spec'])
    if task['kind'] == 'command':
        cli.require_command(spec['argv'][0])
    else:
        executable = cli.require_command('codex')
        cli.ai_command(spec['provider'], [], '', os.environ, batch=True)
        if spec['provider']['kind'] == 'codex':
            try:
                result = subprocess.run([executable, 'login', 'status'], capture_output=True, timeout=10)
            except subprocess.TimeoutExpired:
                raise cli.OperatorError('Codex authentication check timed out.') from None
            if result.returncode:
                raise cli.OperatorError('Codex is not authenticated. Run codex login first.')


def enqueue(db, name, *, scheduled=False):
    task = get(db, name)
    if scheduled and not task['enabled']:
        return None
    try:
        ready(task)
    except (cli.OperatorError, OSError) as error:
        record(db, name, str(error))
        raise
    with jobs.transaction(db):
        active = db.execute('''SELECT j.id FROM jobs j JOIN scheduled_runs r ON r.job_id=j.id
            WHERE r.task_name=? AND j.state IN ('queued','running')''', (name,)).fetchone()
        if active:
            record(db, name, 'Skipped: previous run is still queued or running')
            return None
        job = jobs.submit(db, task['kind'], json.loads(task['spec']), task['workspace'], task['timeout'])
        db.execute('INSERT INTO scheduled_runs(job_id,task_name,revision) VALUES(?,?,?)',
                   (job, name, task['revision']))
        record(db, name, 'Queued ' + job)
    return job


def unit_name(name, suffix):
    return 'rougarou-task-' + name_check(name) + '.' + suffix


def unit_directory():
    return cli.config_dir().parent / 'systemd/user'


def unit_quote(value):
    return json.dumps(str(value).replace('%', '%%'), ensure_ascii=False)


def unit_files(task):
    name = task['name']
    environment = {'XDG_CONFIG_HOME': str(cli.config_dir().parent),
                   'XDG_STATE_HOME': str(jobs.state_dir().parent),
                   'PATH': f'{Path.home()}/.local/bin:{Path.home()}/bin:/usr/local/bin:/usr/bin:/bin'}
    service = MARKER + '[Unit]\nDescription=Enqueue Rougarou task ' + name + '\n\n[Service]\nType=oneshot\n'
    service += 'ExecStart=/usr/local/bin/rougarou tasks _fire ' + name + '\n'
    service += 'UMask=0077\nNoNewPrivileges=yes\nTimeoutStartSec=45\n'
    for key, value in environment.items():
        service += 'Environment=' + unit_quote(key + '=' + value) + '\n'
    timer = (MARKER + '[Unit]\nDescription=Schedule Rougarou task ' + name + '\n\n[Timer]\n'
             'OnCalendar=' + task['calendar'] + '\nPersistent=' + ('true' if task['catch_up'] else 'false') +
             '\nAccuracySec=1s\n\n[Install]\nWantedBy=timers.target\n')
    return {unit_name(name, 'service'): service, unit_name(name, 'timer'): timer}


def managed_file(path):
    cli.check_regular(path, private=True)
    if path.exists() and not path.read_text().startswith(MARKER):
        raise cli.OperatorError(f'Refusing to replace an unmanaged unit: {path}')


def enable(db, name):
    task = get(db, name)
    ready(task)
    tested = db.execute('''SELECT 1 FROM scheduled_runs r JOIN jobs j ON j.id=r.job_id
        WHERE r.task_name=? AND r.revision=? AND j.state='succeeded' LIMIT 1''', (name, task['revision'])).fetchone()
    if not tested:
        raise cli.OperatorError('Run rougarou tasks run ' + name + ' and wait for success before enabling this revision.')
    files = unit_files(task)
    for filename in files:
        managed_file(unit_directory() / filename)
    for filename, contents in files.items():
        cli.atomic_write(unit_directory() / filename, contents, backup=False)
    systemctl('daemon-reload')
    db.execute('UPDATE scheduled_tasks SET enabled=1 WHERE name=?', (name,))
    try:
        systemctl('enable', '--now', unit_name(name, 'timer'))
    except (cli.OperatorError, OSError):
        db.execute('UPDATE scheduled_tasks SET enabled=0 WHERE name=?', (name,))
        raise
    record(db, name, 'Enabled')


def pause(db, name, *, remove=False):
    get(db, name)
    paths = [unit_directory() / unit_name(name, suffix) for suffix in ('timer', 'service')]
    for path in paths:
        managed_file(path)
    db.execute('UPDATE scheduled_tasks SET enabled=0 WHERE name=?', (name,))
    if paths[0].exists():
        systemctl('disable', '--now', unit_name(name, 'timer'))
    if remove:
        for path in paths:
            path.unlink(missing_ok=True)
        systemctl('daemon-reload')
        db.execute('UPDATE scheduled_tasks SET deleted=1 WHERE name=?', (name,))
    record(db, name, 'Removed; history retained' if remove else 'Paused; existing jobs retained')


def history(db, name):
    get(db, name, deleted=True)
    return [dict(row) for row in db.execute('''SELECT j.id,j.state,j.created,j.started,j.finished,
        j.exit_code,r.revision FROM jobs j JOIN scheduled_runs r ON j.id=r.job_id
        WHERE r.task_name=? ORDER BY j.created DESC LIMIT 100''', (name,))]


def main(argv, *, prog='rougarou tasks'):
    cli.require_operator()
    parser = argparse.ArgumentParser(prog=prog, description=__doc__)
    commands = parser.add_subparsers(dest='action', required=True)
    for action in ('add', 'edit'):
        p = commands.add_parser(action, help='save a complete task definition, initially paused')
        p.add_argument('name')
        p.add_argument('--on', required=True, help='systemd calendar, e.g. daily or Mon..Fri 09:00')
        p.add_argument('--timezone', default='UTC', help='IANA timezone (default: UTC)')
        p.add_argument('--catch-up', action='store_true', help='run once after downtime')
        p.add_argument('--workspace', default=str(Path.cwd()))
        p.add_argument('--timeout', type=int, default=3600)
        p.add_argument('--provider', choices=('current', 'codex'), default='current')
        p.add_argument('--model', help='explicit Codex model; otherwise the provider/CLI default')
        p.add_argument('--sandbox', choices=('read-only', 'workspace-write'), default='read-only')
        prompt = p.add_mutually_exclusive_group(required=True)
        prompt.add_argument('--prompt', help='task text, or - for stdin')
        prompt.add_argument('--command', nargs=argparse.REMAINDER, help='trusted unattended command and arguments')
    for action in ('show', 'run', '_fire', 'enable', 'pause', 'remove', 'history', 'logs'):
        p = commands.add_parser(action)
        p.add_argument('name')
    commands.add_parser('list')
    args = parser.parse_args(argv)
    value = definition(args) if args.action in {'add', 'edit'} else None
    with operation(), database() as db:
        if value is not None:
            save(db, args.name, value, edit=args.action == 'edit')
            print(f'Saved paused task {args.name}. Test with: {prog} run {args.name}')
        elif args.action in {'run', '_fire'}:
            job = enqueue(db, args.name, scheduled=args.action == '_fire')
            print(job or 'No job queued (paused or previous run still active).')
        elif args.action == 'enable':
            enable(db, args.name)
            print('Enabled. Check login persistence with: loginctl show-user "$USER" -p Linger')
        elif args.action in {'pause', 'remove'}:
            pause(db, args.name, remove=args.action == 'remove')
            print('Task ' + ('removed' if args.action == 'remove' else 'paused') + '; existing jobs and history retained.')
        elif args.action == 'list':
            for row in db.execute('SELECT name,calendar,enabled FROM scheduled_tasks WHERE deleted=0 ORDER BY name'):
                result = systemctl('show', unit_name(row['name'], 'timer'), '-p', 'NextElapseUSecRealtime', '--value', check=False)
                next_run = result.stdout.strip() if row['enabled'] and result.returncode == 0 else '-'
                print(jobs.safe_text(f"{row['name']}  {'enabled' if row['enabled'] else 'paused'}  {row['calendar']}  next: {next_run or '-'}"))
        elif args.action == 'show':
            task = get(db, args.name, deleted=True)
            task['spec'] = json.loads(task['spec'])
            task['events'] = [dict(row) for row in db.execute('SELECT at,detail FROM scheduled_events WHERE task_name=? ORDER BY id DESC LIMIT 20', (args.name,))]
            print(json.dumps(task, indent=2))
        else:
            runs = history(db, args.name)
            if args.action == 'history':
                print(json.dumps(runs, indent=2))
            elif runs:
                return jobs.main(['logs', runs[0]['id']])
            else:
                print('No runs yet.')
    return 0
