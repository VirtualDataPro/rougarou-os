"""Read-only, bounded local candidates for shell completion. No provider calls."""
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
from zoneinfo import available_timezones

import cli
import jobs


def candidates(kind, action=''):
    if kind == 'timezones':
        return sorted(available_timezones())
    if kind == 'models':
        provider = {'kind': 'codex'} if action == 'codex' else cli.load_config().get('provider', {'kind': 'codex'})
        values = [provider['model']] if provider.get('model') else []
        if provider['kind'] == 'codex':
            path = Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex'))) / 'models_cache.json'
            cli.check_regular(path)
            if path.exists() and path.stat().st_size <= 4 * 1024 * 1024:
                values += [item['slug'] for item in json.loads(path.read_text()).get('models', [])
                           if item.get('visibility') == 'list']
        return values
    if kind not in {'tasks', 'jobs'} or not (jobs.state_dir() / 'jobs.sqlite3').exists():
        return []
    with jobs.read_database() as db:
        if kind == 'tasks':
            if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='scheduled_tasks'").fetchone():
                return []
            where = '1' if action in {'show', 'history', 'logs'} else 'deleted=0'
            if action in {'edit', 'enable'}:
                where += ' AND enabled=0'
            elif action == 'pause':
                where += ' AND enabled=1'
            return [row[0] for row in db.execute('SELECT name FROM scheduled_tasks WHERE ' + where + ' ORDER BY name LIMIT 1000')]
        states = {'cancel': ('queued', 'running'), 'retry': ('failed', 'timed_out', 'interrupted', 'cancelled'),
                  'ack': ('failed', 'timed_out', 'interrupted')}.get(action)
        where = 'WHERE state IN (' + ','.join('?' for _ in states) + ')' if states else ''
        return [row[0] for row in db.execute('SELECT id FROM jobs ' + where + ' ORDER BY created DESC LIMIT 1000', states or ())]


def main(argv):
    try:
        for value in dict.fromkeys(candidates(*argv)):
            # These identifiers need no shell quoting; never render controls or shell syntax.
            if isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_./:+-]{0,255}', value):
                print(value)
    except (OSError, ValueError, TypeError, KeyError, sqlite3.Error, cli.OperatorError):
        pass  # Missing, busy, old or malformed local state must not disrupt Tab.


if __name__ == '__main__':
    main(sys.argv[1:])
