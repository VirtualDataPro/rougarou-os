"""Selection, cancellation and backend boundary tests for the SSH control center."""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

LIB = Path(os.environ.get('ROUGAROU_TEST_LIB', Path(__file__).resolve().parents[1] / 'rootfs/usr/lib/rougarou'))
sys.path.insert(0, str(LIB))
import cli
import jobs
import menu
import tasks


class MenuTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        env = patch.dict(os.environ, {'HOME': str(self.root), 'XDG_STATE_HOME': str(self.root / 'state'),
                                     'XDG_CONFIG_HOME': str(self.root / 'config'), 'CODEX_HOME': str(self.root / 'codex')})
        env.start()
        self.addCleanup(env.stop)
        menu.SELECTION.clear()

    def test_picker_maps_hidden_tag_to_exact_identifier_and_remembers_selection(self):
        with patch.object(menu, 'dialog', return_value='2') as ui:
            self.assertEqual(menu.choose('Jobs', [('First run', 'a' * 16), ('Second run', 'b' * 16)]), 'b' * 16)
        self.assertIn('--no-tags', ui.call_args.args)
        self.assertEqual(menu.SELECTION['Jobs'], '2')

    def test_dialog_escape_raises_back_and_sanitizes_terminal_controls(self):
        with patch.object(menu.subprocess, 'run', return_value=subprocess.CompletedProcess([], 255, '')) as run:
            with self.assertRaises(menu.Back):
                menu.dialog('Unsafe\x1b[2J', '--inputbox', 'Name', '10', '0', '')
        self.assertNotIn('Unsafe\x1b[2J', run.call_args.args[0])
        self.assertEqual(run.call_args.kwargs['env']['DIALOGRC'].split('/')[-1], 'dialogrc')

    def test_model_picker_uses_visible_cache_and_preserves_saved_custom_model(self):
        directory = self.root / 'codex'
        directory.mkdir()
        (directory / 'models_cache.json').write_text(json.dumps({'models': [
            {'slug': 'model-a', 'display_name': 'Model A', 'visibility': 'list'},
            {'slug': 'internal', 'display_name': 'Internal', 'visibility': 'hide'}]}))
        options = menu.model_options({'kind': 'codex'}, 'saved-model')
        self.assertIn(('Model A', 'model-a'), options)
        self.assertIn(('saved-model', 'saved-model'), options)
        self.assertNotIn(('Internal', 'internal'), options)
        with patch.object(menu, 'choose', side_effect=menu.Back):
            with self.assertRaises(menu.Back):
                menu.pick_model({'kind': 'codex'}, 'saved-model')

    def test_model_override_is_emitted_once_for_all_builtin_providers(self):
        for provider in ({'kind': 'codex'}, {'kind': 'ollama', 'model': 'old'},
                         {'kind': 'responses', 'model': 'old', 'endpoint': 'https://example.com/v1', 'authenticated': False}):
            spec = {'provider': provider, 'model': 'selected', 'prompt': 'Review', 'sandbox': 'read-only'}
            argv, _, _ = jobs.command_for({'kind': 'ai', 'spec': json.dumps(spec)})
            self.assertLessEqual(argv.count('--model'), 1)
            self.assertNotIn('old', argv)
            self.assertTrue('selected' in argv or 'model="selected"' in argv)
            self.assertEqual(provider.get('model', 'old'), 'old')

    def existing(self):
        return {'name': 'review', 'kind': 'ai', 'spec': json.dumps({'provider': {'kind': 'ollama', 'model': 'original'},
                'model': 'saved', 'prompt': 'Review $(literal)', 'sandbox': 'read-only'}),
                'workspace': str(self.root), 'timeout': 300, 'calendar': '*-*-* 09:00:00 UTC', 'catch_up': 1}

    def test_edit_preserves_provider_prompt_and_schedule_and_saves_new_model(self):
        original = self.existing()
        with tasks.database() as db:
            tasks.save(db, 'review', original)
        with patch.object(menu, 'choose', side_effect=['model', 'save']), patch.object(menu, 'pick_model', return_value='new-model'), \
                patch.object(menu, 'confirm', return_value=True), patch.object(menu, 'show'):
            menu.draft_task(original)
        with tasks.database() as db:
            saved = tasks.get(db, 'review')
        self.assertEqual(saved['calendar'], original['calendar'])
        self.assertEqual(saved['revision'], 2)
        self.assertEqual(saved['enabled'], 0)
        spec = json.loads(saved['spec'])
        self.assertEqual(spec['provider'], {'kind': 'ollama', 'model': 'original'})
        self.assertEqual(spec['prompt'], 'Review $(literal)')
        self.assertEqual(spec['model'], 'new-model')

    def test_cancelled_editor_does_not_write(self):
        with patch.object(menu, 'choose', side_effect=menu.Back), patch.object(tasks, 'save') as save:
            menu.screen(lambda: menu.draft_task(self.existing()))
        save.assert_not_called()
        self.assertFalse((jobs.state_dir() / 'jobs.sqlite3').exists())

    def test_failed_restore_preview_never_applies(self):
        snapshot = 'b' * 64
        with patch.object(menu, 'choose', side_effect=['list', snapshot, menu.Back]), \
                patch.object(menu, 'data', return_value=[{'id': snapshot, 'time': '2026-10-10T12:00:00Z'}]), \
                patch.object(menu, 'command', return_value=1) as command, patch.object(menu, 'confirm') as confirm:
            menu.screen(menu.backups_menu)
        command.assert_called_once_with('backup', 'restore', snapshot)
        confirm.assert_not_called()

    def test_restore_uses_selected_full_id_and_literal_target(self):
        snapshot, target = 'a' * 64, '/new recovery/$(never-execute)'
        with patch.object(menu, 'choose', side_effect=['list', snapshot, menu.Back]), \
                patch.object(menu, 'data', return_value=[{'id': snapshot, 'time': '2026-10-10T12:00:00Z'}]), \
                patch.object(menu, 'command', return_value=0) as command, patch.object(menu, 'confirm', return_value=True), \
                patch.object(menu, 'field', return_value=target):
            menu.screen(menu.backups_menu)
        self.assertEqual(command.call_args_list[-1].args, ('backup', 'restore', snapshot, '--apply', '--target', target))

    def test_update_applies_exact_reviewed_plan_without_typing_id(self):
        plan = {'plan_id': 'c' * 64, 'download_bytes': 12, 'reboot_may_be_needed': False,
                'packages': [{'name': 'example', 'old_version': '1', 'version': '2', 'channel': 'testing'}]}
        with patch.object(menu, 'choose', side_effect=['plan', 'apply', menu.Back]), patch.object(menu, 'data', return_value=plan), \
                patch.object(menu, 'show'), patch.object(menu, 'field', return_value='VM backup; literal'), \
                patch.object(menu, 'confirm', return_value=True), patch.object(menu, 'command') as command:
            menu.screen(menu.updates_menu)
        command.assert_called_once_with('update', 'apply', '--plan', 'c' * 64, '--recovery-point', 'VM backup; literal', '--confirm', native=True)

    def test_subprocess_keeps_shell_metacharacters_literal(self):
        with patch.object(menu, 'dialog'), patch.object(menu.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, '', '')) as run:
            menu.invoke('backup', 'restore', 'a' * 64, '--target', '/some path/$(id)')
        self.assertEqual(run.call_args.args[0][-1], '/some path/$(id)')
        self.assertNotIn('shell', run.call_args.kwargs)

    def test_opening_lists_does_not_create_queue(self):
        self.assertEqual(menu.rows('scheduled_tasks', 'WHERE deleted=0'), [])
        self.assertFalse((jobs.state_dir() / 'jobs.sqlite3').exists())

    def test_requires_terminal(self):
        with patch.object(cli, 'require_operator'), patch.object(cli.sys, 'stdin', io.StringIO('1\n')), \
                patch.object(cli.sys, 'stdout', io.StringIO()), self.assertRaisesRegex(cli.OperatorError, 'interactive terminal'):
            menu.main([])


if __name__ == '__main__':
    unittest.main()
