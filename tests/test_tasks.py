import argparse
import contextlib
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

LIB = Path(__file__).resolve().parents[1] / 'rootfs/usr/lib/rougarou'
sys.path.insert(0, str(LIB))
import backup
import cli
import jobs
import tasks


class TaskTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        env = patch.dict(os.environ, {'XDG_STATE_HOME': str(self.root / 'state'),
                                     'XDG_CONFIG_HOME': str(self.root / 'config')})
        env.start()
        self.addCleanup(env.stop)
        self.calls = []
        def control(*args, **kwargs):
            self.calls.append(args)
            return subprocess.CompletedProcess(args, 0, '', '')
        service = patch.object(tasks, 'systemctl', side_effect=control)
        service.start()
        self.addCleanup(service.stop)
        self.value = {'calendar': '*-*-* 09:00:00 UTC', 'catch_up': 0, 'kind': 'command',
                      'spec': json.dumps({'argv': ['/usr/bin/printf', '%s', 'safe; $(no shell)']}),
                      'workspace': str(self.root), 'timeout': 60}

    def add(self, db, name='daily'):
        tasks.save(db, name, self.value)

    def succeed(self, db, name='daily'):
        job = tasks.enqueue(db, name)
        jobs.finish(db, job, 'succeeded', exit_code=0)
        return job

    def test_calendar_normalization_timezone_and_injection(self):
        self.assertEqual(tasks.calendar('daily', 'America/Chicago'), '*-*-* 00:00:00 America/Chicago')
        for expression, zone in [('daily\nExecStart=/bin/false', 'UTC'), ('nonsense', 'UTC'),
                                 ('daily', '../bad'), ('daily', 'No/Such_Zone')]:
            with self.subTest(expression=expression, zone=zone), self.assertRaises(cli.OperatorError):
                tasks.calendar(expression, zone)

    def test_names_and_duplicate_creation_are_rejected(self):
        with tasks.database() as db:
            for name in ('../escape', 'a.service\n', '%h', '-invalid', 'a' * 49):
                with self.assertRaises(cli.OperatorError):
                    self.add(db, name)
            self.add(db)
            with self.assertRaises(cli.OperatorError):
                self.add(db)

    def test_task_creation_is_paused_and_units_never_contain_prompt(self):
        with tasks.database() as db:
            self.add(db)
            self.assertFalse(tasks.get(db, 'daily')['enabled'])
            self.assertIsNone(tasks.enqueue(db, 'daily', scheduled=True))
            with self.assertRaises(cli.OperatorError):
                tasks.enable(db, 'daily')
            self.succeed(db)
            tasks.enable(db, 'daily')
            task = tasks.get(db, 'daily')
            self.assertTrue(task['enabled'])
            for name, text in tasks.unit_files(task).items():
                path = tasks.unit_directory() / name
                self.assertEqual(path.read_text(), text)
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                self.assertNotIn('safe;', text)
            self.assertIn('Persistent=false', tasks.unit_files(task)['rougarou-task-daily.timer'])

    def test_overlap_skips_then_allows_next_run_after_terminal_state(self):
        with tasks.database() as db:
            self.add(db)
            first = tasks.enqueue(db, 'daily')
            self.assertIsNone(tasks.enqueue(db, 'daily'))
            db.execute("UPDATE jobs SET state='running' WHERE id=?", (first,))
            self.assertIsNone(tasks.enqueue(db, 'daily'))
            jobs.finish(db, first, 'interrupted')
            second = tasks.enqueue(db, 'daily')
            self.assertNotEqual(first, second)
            self.assertEqual(len(tasks.history(db, 'daily')), 2)
            self.assertEqual(jobs.get_job(db, first)['state'], 'interrupted')

    def test_job_and_task_link_roll_back_together(self):
        with tasks.database() as db:
            self.add(db)
            db.execute("CREATE TRIGGER reject_link BEFORE INSERT ON scheduled_runs BEGIN SELECT RAISE(ABORT,'test'); END")
            with self.assertRaises(sqlite3.IntegrityError):
                tasks.enqueue(db, 'daily')
            self.assertEqual(db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM events').fetchone()[0], 0)

    def test_pause_edit_and_remove_preserve_history_and_require_new_test(self):
        with tasks.database() as db:
            self.add(db)
            first = self.succeed(db)
            tasks.enable(db, 'daily')
            with self.assertRaises(cli.OperatorError):
                tasks.save(db, 'daily', self.value, edit=True)
            tasks.pause(db, 'daily')
            self.assertIsNone(tasks.enqueue(db, 'daily', scheduled=True))
            tasks.save(db, 'daily', self.value, edit=True)
            with self.assertRaises(cli.OperatorError):
                tasks.enable(db, 'daily')
            self.succeed(db)
            tasks.enable(db, 'daily')
            tasks.pause(db, 'daily', remove=True)
            self.assertTrue(tasks.get(db, 'daily', deleted=True)['deleted'])
            self.assertIn(first, [r['id'] for r in tasks.history(db, 'daily')])
            self.assertFalse((tasks.unit_directory() / 'rougarou-task-daily.timer').exists())

    def test_failed_enable_or_pause_leaves_task_disabled(self):
        with tasks.database() as db:
            self.add(db)
            self.succeed(db)
            original = tasks.systemctl
            def fail(*args, **kwargs):
                if args[0] in {'enable', 'disable'}:
                    raise cli.OperatorError('service failure')
                return original(*args, **kwargs)
            with patch.object(tasks, 'systemctl', side_effect=fail):
                with self.assertRaises(cli.OperatorError):
                    tasks.enable(db, 'daily')
                self.assertFalse(tasks.get(db, 'daily')['enabled'])
                db.execute('UPDATE scheduled_tasks SET enabled=1')
                with self.assertRaises(cli.OperatorError):
                    tasks.pause(db, 'daily')
                self.assertFalse(tasks.get(db, 'daily')['enabled'])

    def test_unmanaged_and_symlink_units_are_preserved(self):
        with tasks.database() as db:
            self.add(db)
            self.succeed(db)
            directory = tasks.unit_directory()
            directory.mkdir(parents=True)
            timer = directory / 'rougarou-task-daily.timer'
            timer.write_text('operator unit\n')
            timer.chmod(0o600)
            with self.assertRaises(cli.OperatorError):
                tasks.enable(db, 'daily')
            self.assertEqual(timer.read_text(), 'operator unit\n')
            timer.unlink()
            timer.symlink_to(self.root / 'elsewhere')
            with self.assertRaises(cli.OperatorError):
                tasks.enable(db, 'daily')

    def test_provider_snapshot_and_model_survive_default_changes(self):
        args = argparse.Namespace(workspace=str(self.root), timeout=60, command=None, model='chosen-model',
                                  provider='current', prompt='Read only.', sandbox='read-only',
                                  on='daily', timezone='UTC', catch_up=False)
        with patch.object(cli, 'load_config', return_value={'provider': {'kind': 'codex'}}):
            value = tasks.definition(args)
        with tasks.database() as db, patch.object(tasks, 'ready'):
            tasks.save(db, 'review', value)
            with patch.object(cli, 'load_config', side_effect=AssertionError('must not read changed default')):
                job = jobs.get_job(db, tasks.enqueue(db, 'review'))
                argv, _, prompt = jobs.command_for(job)
            self.assertEqual(prompt, 'Read only.')
            self.assertIn('chosen-model', argv)
            self.assertIn('read-only', argv)
            self.assertEqual(argv[:4], ['codex', '-a', 'never', 'exec'])

    def test_inactive_worker_records_failure_without_queueing(self):
        with tasks.database() as db:
            self.add(db)
            with patch.object(tasks, 'systemctl', return_value=subprocess.CompletedProcess([], 3, '', '')):
                with self.assertRaises(cli.OperatorError):
                    tasks.enqueue(db, 'daily')
            self.assertEqual(tasks.history(db, 'daily'), [])
            detail = db.execute('SELECT detail FROM scheduled_events ORDER BY id DESC LIMIT 1').fetchone()[0]
            self.assertIn('Worker is inactive', detail)

    def test_restored_queue_disables_schedules(self):
        target = self.root / 'recovery'
        original = target / backup.CAPSULE / 'state/jobs.sqlite3'
        original.parent.mkdir(parents=True)
        with tasks.database() as db:
            self.add(db)
            self.succeed(db)
            tasks.enable(db, 'daily')
            with sqlite3.connect(original) as copy:
                db.backup(copy)
        original.chmod(0o600)
        backup.prepare_queue(target)
        with sqlite3.connect(target / 'prepared/rougarou-state/jobs.sqlite3') as restored:
            self.assertEqual(restored.execute('SELECT enabled FROM scheduled_tasks').fetchone()[0], 0)

    def test_cli_command_arguments_and_paths_are_literal(self):
        with patch.object(cli, 'require_operator'), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(tasks.main(['add', 'literal', '--on', 'daily', '--workspace', str(self.root),
                                         '--command', '/usr/bin/printf', '%s', '$(touch nowhere)']), 0)
        with tasks.database() as db:
            spec = json.loads(tasks.get(db, 'literal')['spec'])
        self.assertEqual(spec['argv'][-1], '$(touch nowhere)')


if __name__ == '__main__':
    unittest.main()
