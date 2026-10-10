"""SSH control center. Dialog owns presentation; existing backends own operations."""
import argparse
import datetime
import json
import os
from pathlib import Path
import shlex
import shutil
import sqlite3
import subprocess
import sys
import tempfile

import cli
import jobs
import tasks


class Back(Exception):
    """Cancel the current screen without committing its draft."""


THEME = '''use_shadow = OFF
use_colors = ON
screen_color = (WHITE,BLACK,OFF)
dialog_color = (WHITE,BLACK,OFF)
border_color = (GREEN,BLACK,OFF)
title_color = (GREEN,BLACK,ON)
button_active_color = (BLACK,GREEN,ON)
button_inactive_color = (WHITE,BLACK,OFF)
button_key_active_color = (BLACK,GREEN,ON)
button_key_inactive_color = (GREEN,BLACK,ON)
button_label_active_color = (BLACK,GREEN,ON)
button_label_inactive_color = (WHITE,BLACK,OFF)
inputbox_color = (WHITE,BLACK,OFF)
inputbox_border_color = (GREEN,BLACK,OFF)
menubox_color = (WHITE,BLACK,OFF)
menubox_border_color = (GREEN,BLACK,OFF)
item_color = (WHITE,BLACK,OFF)
item_selected_color = (BLACK,GREEN,ON)
tag_color = (WHITE,BLACK,OFF)
tag_selected_color = (BLACK,GREEN,ON)
check_color = (WHITE,BLACK,OFF)
check_selected_color = (BLACK,GREEN,ON)
tag_key_color = (WHITE,BLACK,ON)
tag_key_selected_color = (BLACK,GREEN,ON)
border2_color = (GREEN,BLACK,OFF)
menubox_border2_color = (GREEN,BLACK,OFF)
inputbox_border2_color = (GREEN,BLACK,OFF)
searchbox_color = (WHITE,BLACK,OFF)
searchbox_title_color = (GREEN,BLACK,ON)
searchbox_border_color = (GREEN,BLACK,OFF)
searchbox_border2_color = (GREEN,BLACK,OFF)
position_indicator_color = (GREEN,BLACK,ON)
uarrow_color = (GREEN,BLACK,ON)
darrow_color = (GREEN,BLACK,ON)
use_scrollbar = ON
'''
SELECTION = {}


def clean(value):
    return jobs.safe_text(str(value))


def short(value, width=48):
    text = clean(value).replace('\n', ' ').replace('\t', ' ').strip()
    return text if len(text) <= width else text[:width - 3] + '...'


def dialog(title, *args):
    size = shutil.get_terminal_size((80, 24))
    if size.columns < 60 or size.lines < 20:
        raise cli.OperatorError('The control center needs at least 60 columns and 20 rows. Enlarge your terminal, then run rougarou menu.')
    with tempfile.TemporaryDirectory(prefix='rougarou-menu-') as directory:
        theme = Path(directory) / 'dialogrc'
        theme.write_text(THEME if 'NO_COLOR' not in os.environ else 'use_colors = OFF\nuse_shadow = OFF\n')
        # Keep ncurses' alternate screen so cancellation also restores the shell.
        result = subprocess.run(['dialog', '--keep-tite', '--stdout', '--clear', '--no-collapse',
                                 '--backtitle', 'ROUGAROU OS  /  CONTROL CENTER',
                                 '--title', clean(title), '--ok-label', 'Select', '--cancel-label', 'Back',
                                 *args], stdout=subprocess.PIPE, text=True,
                                env={**os.environ, 'DIALOGRC': str(theme)})
    if result.returncode in (1, 255):
        raise Back()
    if result.returncode:
        raise cli.OperatorError('The terminal interface could not open. Your shell is available; try TERM=xterm-256color rougarou menu.')
    return result.stdout.rstrip('\n')


def choose(title, entries, description='Choose an item to continue.', *, cancel='Back'):
    size = shutil.get_terminal_size((80, 24))
    height = min(size.lines - 2, max(16, len(entries) + description.count("\n") + 9), 30)
    width = min(size.columns - 2, 106)
    selected = SELECTION.get(title, '1')
    args = ['--no-tags', '--default-item', selected, '--cancel-label', cancel,
            '--menu', clean(description) + '\n\nArrow keys  Move     Enter  Select     Esc  Back',
            str(height), str(width), str(max(3, height - 10))]
    for index, (label, _) in enumerate(entries, 1):
        args.extend([str(index), short(label, width - 10)])
    result = dialog(title, *args)
    if not result.isdigit() or not 1 <= int(result) <= len(entries):
        raise Back()
    SELECTION[title] = result
    return entries[int(result) - 1][1]


def field(title, label, value=''):
    return dialog(title, '--ok-label', 'Continue', '--inputbox', clean(label), '10', '0', str(value))


def confirm(title, text, label='Confirm'):
    try:
        dialog(title, '--defaultno', '--yes-label', label, '--no-label', 'Back',
               '--yesno', clean(text), '0', '0')
        return True
    except Back:
        return False


def show(title, text):
    with tempfile.TemporaryDirectory(prefix='rougarou-view-') as directory:
        path = Path(directory) / 'output'
        path.write_text(clean(text) or 'Completed successfully.')
        try:
            dialog(title, '--exit-label', 'Back', '--textbox', str(path), '0', '0')
        except Back:
            pass


def edit_text(title, text):
    with tempfile.TemporaryDirectory(prefix='rougarou-edit-') as directory:
        path = Path(directory) / 'prompt'
        path.write_text(text)
        return dialog(title, '--ok-label', 'Continue', '--editbox', str(path), '0', '0')


def invoke(*argv, native=False):
    entrypoint = Path(__file__).resolve().parents[2] / 'local/bin/rougarou'
    if native:
        return subprocess.run([sys.executable, str(entrypoint), *argv], check=False)
    dialog('Working', '--infobox', 'Please wait...', '5', '32')
    return subprocess.run([sys.executable, str(entrypoint), *argv], capture_output=True, text=True)


def command(*argv, native=False):
    result = invoke(*argv, native=native)
    if native:
        input('Press Enter to return to the control center...')
    else:
        show(' / '.join(argv[:2]).title() + ('  |  Needs attention' if result.returncode else ''),
             result.stdout + result.stderr)
    return result.returncode


def data(*argv):
    result = invoke(*argv)
    if result.returncode:
        raise cli.OperatorError(result.stderr.strip() or result.stdout.strip() or 'Command failed.')
    return json.loads(result.stdout)


def rows(table, where='', parameters=()):
    if not (jobs.state_dir() / 'jobs.sqlite3').exists():
        return []
    with jobs.read_database() as db:
        if not db.execute('SELECT 1 FROM sqlite_master WHERE type=? AND name=?', ('table', table)).fetchone():
            return []
        return [dict(row) for row in db.execute('SELECT * FROM ' + table + ' ' + where, parameters)]


def model_name(spec):
    return spec.get('model') or spec.get('provider', {}).get('model') or 'Provider default'


def model_options(provider, current=''):
    models = {}
    if provider['kind'] == 'codex':
        path = Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex'))) / 'models_cache.json'
        try:
            cli.check_regular(path)
            if path.stat().st_size <= 4 * 1024 * 1024:
                for item in json.loads(path.read_text()).get('models', []):
                    if item.get('visibility') == 'list':
                        slug = cli.clean_text(item['slug'], 'Model', 256)
                        models[slug] = item.get('display_name', slug)
        except (OSError, ValueError, KeyError, TypeError, cli.OperatorError):
            pass
    elif provider['kind'] == 'ollama' and shutil.which('ollama'):
        try:
            result = subprocess.run(['ollama', 'list'], capture_output=True, text=True, timeout=5)
            if result.returncode == 0:
                models.update((line.split()[0], line.split()[0]) for line in result.stdout.splitlines()[1:] if line.split())
        except (OSError, subprocess.TimeoutExpired):
            pass
    for value in (provider.get('model'), current):
        if value:
            models.setdefault(value, value)
    return [('Provider default', '')] + [(str(label), slug) for slug, label in models.items()] + [('Enter another model...', '__custom__')]


def pick_model(provider, current=''):
    options = model_options(provider, current)
    SELECTION['Models'] = str(next((i for i, (_, value) in enumerate(options, 1) if value == current), 1))
    value = choose('Models', options, f"Provider: {provider['kind']}\nCurrent: {current or 'Provider default'}\nLocal model list; availability is checked when a job runs.")
    return cli.clean_text(field('Models', 'Model identifier', current), 'Model', 256) if value == '__custom__' else value


def pick_provider(current=None):
    configured = cli.load_config().get('provider')
    choices = [('Codex', {'kind': 'codex'})]
    for label, value in [('Keep saved provider', current), ('Configured provider', configured)]:
        if value and value not in [item[1] for item in choices]:
            choices.insert(0, (f"{label}  /  {value['kind']}", value))
    return choose('Provider', choices, 'Choose the provider for this task. Connect other providers from Settings.')


def draft_task(existing=None, *, scheduled=True):
    spec = json.loads(existing['spec']) if existing else {}
    name = existing['name'] if existing else (field('New schedule', 'Give this schedule a name (letters, numbers, hyphens)', '') if scheduled else 'New job')
    if scheduled:
        tasks.name_check(name)
    kind = existing['kind'] if existing else choose(name, [('AI prompt', 'ai'), ('Unattended command', 'command')], 'What should this task run?')
    provider = spec.get('provider') or (pick_provider() if kind == 'ai' else None)
    model = spec.get('model', '')
    prompt = spec.get('prompt', '')
    argv = shlex.join(spec.get('argv', []))
    workspace = existing['workspace'] if existing else str(Path.home() / 'Work')
    timeout = existing['timeout'] if existing else 3600
    schedule = existing['calendar'] if existing else tasks.calendar('daily', 'UTC')
    catch_up = bool(existing and existing['catch_up'])
    sandbox = spec.get('sandbox', 'read-only')
    while True:
        entries = [('Prompt  /  ' + short(prompt or 'Add instructions', 52), 'prompt'),
                   (f"Provider  /  {provider['kind']}", 'provider'),
                   ('Model  /  ' + (model or provider.get('model') or 'Provider default'), 'model'),
                   ('Permissions  /  ' + sandbox, 'sandbox')] if kind == 'ai' else [('Command  /  ' + short(argv or 'Add executable and arguments'), 'argv')]
        entries += [('Workspace  /  ' + workspace, 'workspace'), (f'Runtime limit  /  {timeout} seconds', 'timeout')]
        if scheduled:
            entries += [('Schedule  /  ' + schedule, 'schedule'), ('After downtime  /  ' + ('Catch up once' if catch_up else 'Wait for next run'), 'catchup')]
        entries.append(('Save paused schedule' if scheduled else 'Review and queue job', 'save'))
        action = choose(name + ' / Editor', entries, 'Select any field to edit. Esc discards this draft.\n' + ('Saving requires a new successful test before enabling.' if scheduled else 'Runs through your managed worker.'))
        try:
            if action == 'prompt':
                prompt = edit_text('Task instructions', prompt)
            elif action == 'provider':
                provider, model = pick_provider(provider), ''
            elif action == 'model':
                model = pick_model(provider, model)
            elif action == 'sandbox':
                sandbox = choose('Permissions', [('Read only  /  inspect and report', 'read-only'), ('Workspace write  /  allow changes in the workspace', 'workspace-write')])
            elif action == 'argv':
                argv = field('Unattended command', 'Executable and arguments; shell quoting accepted, no shell evaluation.\nSet another CLI\'s model using its own arguments.', argv)
            elif action == 'workspace':
                workspace = field('Workspace', 'Existing working directory', workspace)
            elif action == 'timeout':
                text = field('Runtime limit', 'Maximum seconds (1 to 604800)', str(timeout))
                if not text.isdigit() or not 1 <= int(text) <= 604800:
                    show('Runtime limit', 'Enter a whole number from 1 to 604800.')
                else:
                    timeout = int(text)
            elif action == 'catchup':
                catch_up = choose('After downtime', [('Wait for the next scheduled run', False), ('Catch up once when this host returns', True)])
            elif action == 'schedule':
                expression = choose('Frequency', [('Every hour', 'hourly'), ('Every day', 'daily'), ('Weekdays', 'Mon..Fri'), ('Every week', 'weekly'), ('Custom calendar...', 'custom')])
                if expression in ('daily', 'Mon..Fri', 'weekly'):
                    at = field('Time of day', '24-hour time (HH:MM)', '09:00')
                    expression = {'daily': '*-*-*', 'weekly': 'Mon', 'Mon..Fri': 'Mon..Fri'}[expression] + ' ' + at
                elif expression == 'custom':
                    expression = field('Custom calendar', 'systemd calendar expression without timezone', 'Mon..Fri 09:00')
                zone = field('Timezone', 'IANA timezone', schedule.rsplit(' ', 1)[-1])
                schedule = tasks.calendar(expression, zone)
            elif action == 'save':
                if kind == 'ai' and prompt == '-':
                    raise cli.OperatorError('Enter task instructions; a single hyphen is reserved for CLI input.')
                arguments = argparse.Namespace(workspace=workspace, timeout=timeout, command=shlex.split(argv) if kind == 'command' else None,
                    model=model or None, provider='codex' if kind == 'ai' else 'current', prompt=prompt, sandbox=sandbox,
                    on='daily', timezone='UTC', catch_up=catch_up)
                value = tasks.definition(arguments)
                saved_spec = json.loads(value['spec'])
                if kind == 'ai':
                    cli.validate_provider(provider)
                    if provider['kind'] == 'command':
                        raise cli.OperatorError('Use an unattended command task for this provider.')
                    saved_spec['provider'] = provider
                value.update(calendar=schedule, spec=json.dumps(saved_spec))
                summary = f"{name}\n\nWorkspace: {workspace}\nModel: {model_name(saved_spec) if kind == 'ai' else 'Set by command'}\nRuntime limit: {timeout}s\n" + (f'Schedule: {schedule}\nSaved paused.' if scheduled else 'Queue this job now?')
                if not confirm('Review schedule' if scheduled else 'Review job', summary, 'Save' if scheduled else 'Queue'):
                    continue
                if scheduled:
                    with tasks.operation(), tasks.database() as db:
                        tasks.save(db, name, value, edit=existing is not None)
                    show('Schedule saved', f'{name} is paused. Select Run now to test it, then enable its schedule after the test succeeds.')
                else:
                    with jobs.database() as db:
                        identifier = jobs.submit(db, kind, saved_spec, value['workspace'], timeout)
                    show('Job queued', f'Your job is queued. Open Jobs to follow its progress.\n\n{identifier}')
                return
        except (cli.OperatorError, OSError, ValueError) as error:
            show("Check this field", str(error))



def task_detail(name):
    while True:
        task = rows('scheduled_tasks', 'WHERE name=? AND deleted=0', (name,))
        if not task:
            return
        task = task[0]
        spec = json.loads(task['spec'])
        description = f"{'ENABLED' if task['enabled'] else 'PAUSED'}  /  {task['calendar']}\nModel: {model_name(spec) if task['kind'] == 'ai' else 'Command'}\nWorkspace: {task['workspace']}"
        if task['enabled']:
            result = tasks.systemctl('show', tasks.unit_name(name, 'timer'), '-p', 'NextElapseUSecRealtime', '--value', check=False)
            description += '\nNext run: ' + (result.stdout.strip() or 'Check timer status')
        entries = [('Run now  /  test this definition', 'run'), ('Run history', 'history'), ('Latest output', 'logs')]
        entries += [('Pause schedule', 'pause')] if task['enabled'] else [('Enable tested schedule', 'enable'), ('Edit schedule and model', 'edit')]
        entries += [('Full definition and events', 'show'), ('Remove schedule', 'remove')]
        action = choose(name, entries, description)
        if action == 'edit':
            screen(lambda: draft_task(task))
        elif action == 'history':
            with tasks.database() as db:
                history = tasks.history(db, name)
            screen(lambda: job_list(history, name + ' / History'))
        elif action not in {'run', 'enable', 'remove'} or confirm(name, f'{action.capitalize()} this schedule?\nExisting history is retained.', action.capitalize()):
            command('tasks', action, name)
            if action == 'remove':
                return


def tasks_menu():
    while True:
        records = rows('scheduled_tasks', 'WHERE deleted=0 ORDER BY name')
        entries = [(' +  Create schedule', None)] + [(f"{'ON' if row['enabled'] else 'PAUSED':6}  {row['name']}  /  {row['calendar']}", row['name']) for row in records]
        name = choose('Schedules', entries, f'{len(records)} saved  /  Select a schedule to manage it.' if records else 'Your first schedule starts here. Create it, test it, then enable it.')
        screen(lambda: draft_task() if name is None else task_detail(name))


def job_detail(identifier):
    while True:
        job = data('jobs', 'show', identifier, '--json')
        spec = job['spec']
        description = f"{job['state'].upper()}  /  {identifier}\nModel: {model_name(spec) if job['kind'] == 'ai' else 'Command'}\n{short(spec.get('prompt') or shlex.join(spec.get('argv', [])), 100)}"
        entries = [('Output and logs', 'logs'), ('Full details and events', 'show')]
        if job['state'] in {'queued', 'running'}:
            entries += [('Cancel job', 'cancel')]
        if job['state'] in jobs.TERMINAL - {'succeeded'}:
            entries += [('Retry with saved model and settings', 'retry')]
        if job['state'] in jobs.ATTENTION:
            entries += [('Mark as reviewed', 'ack')]
        action = choose('Job / ' + identifier, entries, description)
        if action not in {'cancel', 'retry'} or confirm('Review action', f'{action.capitalize()} this job?\n{identifier}', action.capitalize()):
            command('jobs', action, identifier)


def job_list(records, title='Jobs'):
    if not records:
        show(title, 'No runs yet. Run a schedule or create a job to see its progress here.')
        return
    entries = []
    for row in records:
        when = datetime.datetime.fromtimestamp(row['created']).strftime('%m-%d %H:%M')
        spec = json.loads(row['spec']) if 'spec' in row else {}
        label = short(spec.get('prompt') or shlex.join(spec.get('argv', [])) or row['id'], 42)
        entries.append((f"{row['state'].upper():11} {when}  {row['id'][:6]}  {label}", row['id']))
    identifier = choose(title, entries, 'Newest first. Select a run for its output and available actions.')
    screen(lambda: job_detail(identifier))


def jobs_menu():
    while True:
        action = choose('Jobs', [('Browse recent runs', 'list'), ('Create a job', 'new'), ('Mark unsuccessful runs as reviewed', 'ack')], 'Persistent work, progress, and output. Model selection is available when creating a job.')
        if action == 'new':
            screen(lambda: draft_task(scheduled=False))
        elif action == 'list':
            screen(lambda: job_list(rows('jobs', 'ORDER BY created DESC LIMIT 200')))
        elif confirm('Review all failures', 'Mark all current failed, timed-out and interrupted jobs as reviewed?\nHistory and logs are retained.', 'Mark reviewed'):
            command('jobs', 'ack', '--all')


def workers_menu():
    while True:
        status = jobs.snapshot()
        worker = status['worker']
        alive = worker and worker['state'] == 'running' and worker['age_seconds'] < 10
        counts = status['counts']
        text = f"Your worker  /  {'RUNNING' if alive else 'OFFLINE'}\n{counts.get('queued', 0)} queued  /  {counts.get('running', 0)} running\nOne worker per operator. Each job carries its own provider and model."
        action = choose('Workers', [(label, value) for label, value in [('Service details', 'status'), ('Start worker', 'start'), ('Stop worker', 'stop'), ('Restart worker', 'restart'), ('Enable at login', 'enable'), ('Disable and stop', 'disable'), ('View worker logs', 'logs')]], text)
        if action == 'logs':
            result = subprocess.run(['journalctl', '--user', '-u', jobs.UNIT, '-n', '100', '--no-pager'], capture_output=True, text=True)
            show('Worker logs', result.stdout + result.stderr)
        elif action not in {'stop', 'restart', 'disable'} or confirm('Worker service', f'{action.capitalize()} your worker?\nActive work may be interrupted; queued work is retained.', action.capitalize()):
            command('service', action)


def updates_menu():
    while True:
        action = choose('Updates', [('Review available updates', 'plan'), ('Refresh signed package metadata', 'refresh'), ('Current status', 'status'), ('Recovery guide', 'recovery')], 'Review package changes before applying them.\nUpdates are system operations and do not use an AI model.')
        if action == 'plan':
            plan = data('update', 'plan', '--json')
            if not plan['packages']:
                show('Updates', 'You are up to date with the last refreshed metadata.')
                continue
            summary = '\n'.join(f"{p['name']}  {p['old_version'] or '(new)'} -> {p['version']}  [{p['channel']}]" for p in plan['packages'])
            show('Review package changes', f"{len(plan['packages'])} changes  /  {plan['download_bytes'] / 1048576:.1f} MiB\n\n{summary}\n\nReboot may be needed: {plan['reboot_may_be_needed']}\nCreate a VM recovery point before applying.")
            if choose('Reviewed update plan', [('Back to updates', 'back'), ('Apply these changes...', 'apply')], 'The reviewed plan is selected automatically; it will be checked again before installation.') == 'apply':
                recovery = field('Recovery point', 'Reference of your existing VM snapshot or backup')
                if recovery and confirm('Apply updates', f'Apply {len(plan["packages"])} reviewed package changes?\nRecovery point: {recovery}\nNo automatic reboot.', 'Apply'):
                    command('update', 'apply', '--plan', plan['plan_id'], '--recovery-point', recovery, '--confirm', native=True)
        else:
            command('update', action, native=action == 'refresh')


def backups_menu():
    while True:
        action = choose('Backups', [('Browse recovery points', 'list'), ('Create backup', 'create'), ('Verify backup data', 'check'), ('Configure repository', 'init')], 'Encrypted backups and staged recovery.\nBackups do not use an AI model.')
        if action == 'list':
            records = data('backup', 'list', '--json')
            if not records:
                show('Backups', 'No recovery points yet. Create a backup to get started.')
                continue
            snapshot = choose('Recovery points', [(f"{row['time'][:19]}  /  {row['id'][:8]}", row['id']) for row in sorted(records, key=lambda r: r['time'], reverse=True)], 'Select a backup to preview its contents and restore options.')
            if command('backup', 'restore', snapshot) == 0 and confirm('Stage recovery', 'Restore the selected backup into a new directory for inspection?\nYour live workspace is preserved.', 'Continue'):
                target = field('Recovery directory', 'New directory, or leave blank for the default')
                command('backup', 'restore', snapshot, '--apply', *(['--target', target] if target else []))
        elif action == 'init':
            repository = field('Backup repository', 'Repository directory')
            password = field('Backup password', 'Existing private password file')
            if not repository or not password:
                continue
            existing = choose('Repository type', [('Create a new encrypted repository', False), ('Attach an existing repository', True)])
            include = field('Additional data', 'Optional additional service-data directory')
            if confirm('Configure backups', f'Repository: {repository}\nPassword file: {password}', 'Save'):
                command('backup', 'init', '--repository', repository, '--password-file', password, *(['--existing'] if existing else []), *(['--include', include] if include else []))
        else:
            command('backup', action, *(['--read-data'] if action == 'check' else []))


def settings_menu():
    while True:
        action = choose('Settings', [('Connect or choose an AI provider', 'provider'), ('Accounts and setup', 'setup'), ('Notification status', 'notify-status'), ('Configure notifications', 'notify-configure'), ('Enable notifications', 'notify-enable'), ('Disable notifications', 'notify-disable'), ('Send test notification', 'notify-test'), ('Docker containers', 'docker'), ('Podman containers', 'podman'), ('Set up rootless Docker', 'docker-setup')])
        if action.startswith('notify-'):
            command('notify', action[7:], native=action == 'notify-configure')
        elif action in {'docker', 'podman'}:
            result = subprocess.run([action, 'ps', '-a'], capture_output=True, text=True)
            show('Containers', result.stdout + result.stderr)
        elif action == 'docker-setup':
            subprocess.run(['/usr/local/bin/rougarou-docker', 'setup'], check=False)
        else:
            command(action, native=True)


def screen(function):
    try:
        function()
    except Back:
        pass
    except (cli.OperatorError, OSError, ValueError, sqlite3.Error) as error:
        show('Needs attention', str(error))


def main(argv):
    if argv:
        raise cli.OperatorError('Usage: rougarou menu')
    cli.require_operator()
    cli.require_terminal()
    cli.require_command('dialog')
    actions = [('Schedules    /  Plan recurring work', tasks_menu), ('Jobs         /  Follow progress and output', jobs_menu),
               ('Workers      /  Manage execution', workers_menu), ('Updates      /  Review system changes', updates_menu),
               ('Backups      /  Protect and recover data', backups_menu), ('Start agent  /  Open an interactive session', lambda: command('ai', native=True)),
               ('Host health  /  Check services and storage', lambda: command('doctor')), ('Settings     /  Providers, accounts, containers', settings_menu)]
    while True:
        try:
            status = jobs.snapshot()
            counts = status['counts']
            worker = status['worker']
            healthy = worker and worker['state'] == 'running' and worker['age_seconds'] < 10
            description = f"{os.uname().nodename}  /  Worker {'running' if healthy else 'offline'}\n{counts.get('queued', 0)} queued   {counts.get('running', 0)} running   {sum(status.get('attention_counts', {}).values())} need review"
            action = choose('Overview', actions, description, cancel='Exit to shell')
        except Back:
            return 0
        screen(action)
