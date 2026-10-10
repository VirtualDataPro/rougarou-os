"""Real Bash completion contexts and read-only local candidates."""
import contextlib
import io
import json
import os
from pathlib import Path
import pty
import select
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(os.environ.get('ROUGAROU_TEST_ROOT', Path(__file__).resolve().parents[1] / 'rootfs'))
sys.path.insert(0, str(ROOT / 'usr/lib/rougarou'))
import cli
import completion
import jobs
import tasks

SCRIPT = ROOT / 'usr/share/bash-completion/completions/rougarou'
BASH_COMPLETION = Path('/usr/share/bash-completion/bash_completion')


class CompletionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        env = patch.dict(os.environ, {'HOME': str(self.root), 'XDG_STATE_HOME': str(self.root / 'state'),
                                     'XDG_CONFIG_HOME': str(self.root / 'config'), 'CODEX_HOME': str(self.root / 'codex')})
        env.start()
        self.addCleanup(env.stop)

    def complete(self, words):
        if not BASH_COMPLETION.exists():
            self.skipTest('Bash completion helpers unavailable')
        line = ' '.join(shlex.quote(word) if ' ' in word else word for word in words)
        code = f'''source {shlex.quote(str(BASH_COMPLETION))}
source {shlex.quote(str(SCRIPT))}
COMP_WORDS=({' '.join(shlex.quote(word) for word in words)})
COMP_CWORD={len(words)-1}
COMP_LINE={shlex.quote(line)}
COMP_POINT=${{#COMP_LINE}}
_rougarou
printf '%s\\0' "${{COMPREPLY[@]}}"
'''
        result = subprocess.run(['bash', '--noprofile', '--norc', '-c', code], text=True, capture_output=True, timeout=3)
        self.assertEqual(result.returncode, 0, result.stderr)
        # compopt reports no active completion in a noninteractive direct call.
        self.assertNotIn('Traceback', result.stderr)
        return [word for word in result.stdout.split('\0') if word]

    def fixture(self):
        with tasks.database() as db:
            value = {'calendar': '*-*-* 09:00:00 UTC', 'catch_up': 0, 'kind': 'command',
                     'spec': json.dumps({'argv': ['/bin/true']}), 'workspace': str(self.root), 'timeout': 30}
            for name in ('paused-review', 'enabled-review', 'removed-review'):
                tasks.save(db, name, value)
            db.execute("UPDATE scheduled_tasks SET enabled=1 WHERE name='enabled-review'")
            db.execute("UPDATE scheduled_tasks SET deleted=1 WHERE name='removed-review'")
            self.queued = jobs.submit(db, 'command', {'argv': ['/bin/true']}, self.root)
            self.failed = jobs.submit(db, 'command', {'argv': ['/bin/true']}, self.root)
            jobs.finish(db, self.failed, 'failed', 'fixture', 1)

    def test_static_contexts_and_options(self):
        for words, expected in [(['rougarou', 'ta'], ['tasks']), (['rougarou', 'tasks', 'en'], ['enable']),
                                (['rougarou', 'sche'], ['schedule']), (['rougarou', 'schedule', 'en'], ['enable']),
                                (['rougarou', 'service', 're'], ['restart']), (['rougarou', 'jobs', 'logs', '--t'], ['--tail']),
                                (['rougarou', 'backup', 'check', '--r'], ['--read-data']),
                                (['rougarou', 'tasks', 'add', 'new', '--sandbox', 'read'], ['read-only']),
                                (['rougarou', 'tasks', 'add', 'new', '--sandbox=read'], ['read-only'])]:
            with self.subTest(words=words):
                self.assertEqual(self.complete(words), expected)
        self.assertNotIn('_fire', self.complete(['rougarou', 'tasks', '']))
        self.assertNotIn('--apply', self.complete(['rougarou', 'backup', 'list', '--']))
        self.assertEqual(self.complete(['rougarou', 'tasks', 'add', 'new', '--prompt', '']), [])

    def test_live_ids_are_filtered_by_action_and_position(self):
        self.fixture()
        self.assertEqual(self.complete(['rougarou', 'tasks', 'enable', 'p']), ['paused-review'])
        self.assertEqual(self.complete(['rougarou', 'schedule', 'enable', 'p']), ['paused-review'])
        self.assertEqual(self.complete(['rougarou', 'tasks', 'pause', 'e']), ['enabled-review'])
        self.assertEqual(self.complete(['rougarou', 'tasks', 'remove', 'r']), [])
        self.assertEqual(self.complete(['rougarou', 'tasks', 'history', 'r']), ['removed-review'])
        self.assertIn(self.queued, self.complete(['rougarou', 'jobs', 'cancel', '']))
        self.assertNotIn(self.failed, self.complete(['rougarou', 'jobs', 'cancel', '']))
        self.assertIn(self.failed, self.complete(['rougarou', 'jobs', 'retry', '']))
        self.assertEqual(self.complete(['rougarou', 'jobs', 'logs', self.failed, 'a']), [])
        self.assertEqual(self.complete(['rougarou', 'jobs', 'logs', '--tail', '10', self.failed[:8]]), [self.failed])
        self.assertEqual(self.complete(['rougarou', 'tasks', 'edit', '--timeout=30', 'p']), ['paused-review'])

    def test_path_completion_preserves_spaces_and_does_not_execute_shell_text(self):
        path = self.root / 'work space'
        path.mkdir()
        self.assertEqual(self.complete(['rougarou', 'jobs', 'submit', '--workspace', str(self.root / 'work')]), [str(path)])
        marker = self.root / 'do-not-create'
        self.complete(['rougarou', 'jobs', 'submit', '--workspace', f'$(touch {marker})'])
        self.assertFalse(marker.exists())

    def test_command_remainder_never_suggests_rougarou_options(self):
        self.assertNotIn('--model', self.complete(['rougarou', 'tasks', 'add', 'new', '--command', 'printf', '--m']))
        self.assertNotIn('--model', self.complete(['rougarou', 'jobs', 'run', '--', 'printf', '--m']))
        self.assertIn('printf', self.complete(['rougarou', 'jobs', 'run', '--', 'print']))

    def test_local_models_use_selected_provider_and_reject_unsafe_identifiers(self):
        directory = self.root / 'codex'
        directory.mkdir()
        cache = directory / 'models_cache.json'
        cache.write_text(json.dumps({'models': [{'slug': 'fixture-model', 'visibility': 'list'},
                                             {'slug': 'hidden-model', 'visibility': 'hide'},
                                             {'slug': 'bad\nvalue', 'visibility': 'list'}]}))
        cli.save_config({'schema_version': 1, 'provider': {'kind': 'ollama', 'model': 'local-model'}})
        self.assertEqual(self.complete(['rougarou', 'jobs', 'submit', '--model', '']), ['local-model'])
        self.assertEqual(self.complete(['rougarou', 'tasks', 'add', 'new', '--provider', 'codex', '--model', '']), ['fixture-model'])
        self.assertEqual(self.complete(['rougarou', 'tasks', 'add', 'new', '--provider=codex', '--model=fi']), ['fixture-model'])
        cache.write_text('{invalid')
        self.assertEqual(self.complete(['rougarou', 'tasks', 'add', 'new', '--provider', 'codex', '--model', '']), [])

    def test_missing_old_or_locked_state_fails_quietly_without_writes(self):
        self.assertEqual(completion.candidates('tasks', 'run'), [])
        self.assertFalse((self.root / 'state').exists())
        self.fixture()
        path = jobs.state_dir() / 'jobs.sqlite3'
        with jobs.database() as db:
            db.execute('BEGIN EXCLUSIVE')
            result = self.complete(['rougarou', 'jobs', 'cancel', ''])
            # WAL permits readers during a writer transaction. Completion remains bounded.
            self.assertIn(self.queued, result)
        with patch.object(completion.jobs, 'read_database', side_effect=cli.OperatorError('busy')):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                completion.main(['jobs', 'cancel'])
            self.assertEqual(output.getvalue(), '')
        self.assertTrue(path.exists())

    @unittest.skipUnless(shutil.which('bash') and BASH_COMPLETION.exists(), 'Bash completion unavailable')
    def test_real_tab_keys_expand_command_action_and_existing_schedule(self):
        self.fixture()
        rc = self.root / 'rc'
        rc.write_text(f'source {shlex.quote(str(BASH_COMPLETION))}\nsource {shlex.quote(str(SCRIPT))}\n'
                      'PS1="READY> "\nrougarou() { printf "RESULT:%s:%s:%s\\n" "$1" "$2" "${3-}"; }\n')
        child, master = pty.fork()
        if child == 0:
            os.execvpe('bash', ['bash', '--noprofile', '--rcfile', str(rc), '-i'], {**os.environ, 'TERM': 'xterm'})
        data, status, sent = bytearray(), None, False
        deadline = time.monotonic() + 5
        try:
            while time.monotonic() < deadline:
                if select.select([master], [], [], .05)[0]:
                    try:
                        data.extend(os.read(master, 16384))
                    except OSError:
                        break
                if b'READY> ' in data and not sent:
                    os.write(master, b'rougarou ta\ten\tpa\t\r exit\r')
                    sent = True
                done, value = os.waitpid(child, os.WNOHANG)
                if done:
                    status = value
                    break
            self.assertIn(b'RESULT:tasks:enable:paused-review', data)
        finally:
            os.close(master)
            if status is None:
                os.kill(child, signal.SIGKILL)
                os.waitpid(child, 0)


if __name__ == '__main__':
    unittest.main()
