"""Real Bash/Starship PTYs and isolated, backup-first home migrations; no APIs."""
import errno
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import pty
import pwd
import re
import select
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest


INSTALLED_USER = sys.argv[2] if len(sys.argv) == 3 and sys.argv[1] == '--installed' else None
ROOT = Path('/') if INSTALLED_USER else Path(__file__).resolve().parents[1] / 'rootfs'
SHARED = ROOT / 'usr/share/rougarou/bashrc'
TEMPLATE = ROOT / 'usr/share/rougarou/starship.toml'
HELPER = ROOT / 'usr/lib/rougarou-system/configure-shell'
BASH = shutil.which('bash')
loader = importlib.machinery.SourceFileLoader('configure_shell', str(HELPER))
spec = importlib.util.spec_from_loader(loader.name, loader)
setup = importlib.util.module_from_spec(spec)
loader.exec_module(setup)
# Resolve packaged templates for isolated source-tree tests too.
setup.configure.__kwdefaults__['herdr_template'] = ROOT / 'usr/share/rougarou/herdr.toml'
ALIASES = {
    'holdmybeer': 'sudo su -', '..': 'cd ..', '...': 'cd ../..', '....': 'cd ../../..',
    'd': 'docker', 'ff': "fzf --preview 'bat --style=numbers --color=always {}'",
    'g': 'git', 'gcad': 'git commit -a --amend', 'gcam': 'git commit -a -m',
    'gcm': 'git commit -m', 'ls': 'eza -lh --group-directories-first --icons=auto',
    'lsa': 'ls -a', 'lt': 'eza --tree --level=2 --long --icons --git', 'lta': 'lt -a',
}


def env(home):
    return {'PATH': '/usr/local/bin:/usr/bin:/bin', 'HOME': str(home), 'TERM': 'xterm-256color',
            'INPUTRC': '/dev/null', 'HISTFILE': '/dev/null', 'LC_ALL': 'C.UTF-8',
            'STARSHIP_CONFIG': str(TEMPLATE), 'ROUGAROU_WELCOME_SHOWN': '1'}


class Shell:
    def __init__(self, home, login=False, root=False, custom_config=None,
                 ssh_connection=None, ssh_tty=None, term='xterm-256color'):
        self.reaped = False
        self.pending = b''
        environment = env(home)
        if INSTALLED_USER:
            environment.pop('STARSHIP_CONFIG', None)
        if custom_config:
            environment['STARSHIP_CONFIG'] = str(custom_config)
        environment['TERM'] = term
        for key, value in (('SSH_CONNECTION', ssh_connection), ('SSH_TTY', ssh_tty)):
            if value is not None:
                environment[key] = value
        self.pid, self.fd = pty.fork()
        if self.pid == 0:
            if INSTALLED_USER:
                user = 'root' if root else INSTALLED_USER
                # runuser --login normally removes the SSH context; preserve
                # only the test inputs that sshd would supply to a login shell.
                args = ['runuser', '--login', '--whitelist-environment=' + ','.join((
                    'SSH_CONNECTION', 'SSH_TTY', 'STARSHIP_CONFIG', 'INPUTRC',
                    'HISTFILE', 'LC_ALL', 'ROUGAROU_WELCOME_SHOWN')), user] if login else [
                    'runuser', '-u', user, '--', BASH, '-i']
                os.execve('/usr/sbin/runuser', args, environment)
            else:
                rc = home / ('.login-fixture' if login else '.bashrc')
                os.execve(BASH, [BASH, '--noprofile', '--rcfile', str(rc), '-i'], environment)
        self.startup = self.command("printf '\\nSHELL_READY\\n'")

    def until(self, marker, timeout=8):
        output, self.pending = self.pending, b''
        deadline = time.monotonic() + timeout
        while marker not in output:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AssertionError(f'PTY timeout: {output!r}')
            if select.select([self.fd], [], [], remaining)[0]:
                try:
                    part = os.read(self.fd, 65536)
                except OSError as error:
                    if error.errno == errno.EIO:
                        raise AssertionError(f'PTY exited: {output!r}') from None
                    raise
                if not part:
                    raise AssertionError(f'PTY EOF: {output!r}')
                output += part
        end = output.index(marker) + len(marker)
        self.pending = output[end:]
        return output[:end]

    def command(self, command):
        os.write(self.fd, (command + "; printf '\\nROUGAROU_DONE\\n'\n").encode())
        return self.until(b'\r\nROUGAROU_DONE\r\n')

    def close(self):
        if not self.reaped:
            try:
                os.kill(self.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            os.waitpid(self.pid, 0)
            self.reaped = True
        os.close(self.fd)


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)

    def test_preserves_local_bytes_permissions_and_backup_then_is_idempotent(self):
        rc = self.home / '.bashrc'
        original = b"# personal settings\nalias g='git --no-pager'\nexport OTHER=kept\n"
        rc.write_bytes(original)
        rc.chmod(0o640)
        setup.configure(self.home, TEMPLATE)
        self.assertEqual(rc.read_bytes(), original + setup.BLOCK)
        self.assertEqual(rc.stat().st_mode & 0o777, 0o640)
        backups = list(self.home.glob('.bashrc.rougarou-backup-*'))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), original)
        self.assertEqual(backups[0].stat().st_mode & 0o777, 0o600)
        setup.configure(self.home, TEMPLATE)
        self.assertEqual(list(self.home.glob('.bashrc.rougarou-backup-*')), backups)
        self.assertEqual((self.home / '.config/starship.toml').read_bytes(), TEMPLATE.read_bytes())

    def test_existing_starship_and_custom_overrides_after_block_are_preserved(self):
        setup.configure(self.home, TEMPLATE)
        theme = self.home / '.config/starship.toml'
        theme.write_text('format = "custom"\n')
        rc = self.home / '.bashrc'
        rc.write_bytes(rc.read_bytes() + b"alias g='personal'\n")
        before = rc.read_bytes()
        setup.configure(self.home, TEMPLATE)
        self.assertEqual(theme.read_text(), 'format = "custom"\n')
        self.assertEqual(rc.read_bytes(), before)

    def test_herdr_disables_upstream_checks_only_when_config_is_missing(self):
        setup.configure(self.home, TEMPLATE)
        config = self.home / '.config/herdr/config.toml'
        self.assertEqual(config.read_text(), '[update]\nversion_check = false\nmanifest_check = false\n')
        config.write_text('[operator]\ncustom = true\n')
        setup.configure(self.home, TEMPLATE)
        self.assertEqual(config.read_text(), '[operator]\ncustom = true\n')

    def test_legacy_migration_removes_only_four_exact_noop_macros(self):
        lines = [f'"\\e[100;{m}:3u": ""\n'.encode() for m in (5, 69, 133, 197)]
        retained = b'# keep comment\n"\\e[100;5:3u": "custom"\n"\\e[100;5u": ""\nset editing-mode vi\n'
        path = self.home / '.inputrc'
        original = b''.join(lines) + retained
        path.write_bytes(original)
        setup.configure(self.home, TEMPLATE, legacy=True)
        self.assertEqual(path.read_bytes(), retained)
        backups = list(self.home.glob('.inputrc.rougarou-backup-*'))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), original)
        setup.configure(self.home, TEMPLATE, legacy=True)
        self.assertEqual(list(self.home.glob('.inputrc.rougarou-backup-*')), backups)

    def test_symlinks_and_ambiguous_blocks_are_refused_without_target_changes(self):
        victim = self.home / 'personal'
        victim.write_bytes(b'untouched')
        rc = self.home / '.bashrc'
        rc.symlink_to(victim)
        with self.assertRaises(setup.ShellError):
            setup.configure(self.home, TEMPLATE)
        self.assertEqual(victim.read_bytes(), b'untouched')
        rc.unlink()
        rc.write_bytes(setup.START + b'missing closing marker\n')
        before = rc.read_bytes()
        with self.assertRaises(setup.ShellError):
            setup.configure(self.home, TEMPLATE)
        self.assertEqual(rc.read_bytes(), before)
        self.assertFalse(list(self.home.glob('*.rougarou-backup-*')))


@unittest.skipUnless(BASH, 'Bash required')
class ShellTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        (self.home / '.bashrc').write_text("PS1='DEBIAN_DEFAULT> '\nalias personal='printf preserved'\n" +
            setup.BLOCK.decode().replace('/usr/share/rougarou/bashrc', str(SHARED)))
        # Debian may load profile.d before the user's .bashrc resets PS1.
        (self.home / '.login-fixture').write_text('. ' + shlex.quote(str(SHARED)) + '\n. ' + shlex.quote(str(self.home / '.bashrc')) + '\n')

    def shell(self, **kwargs):
        shell = Shell(self.home, **kwargs)
        self.addCleanup(shell.close)
        return shell

    def test_login_and_nonlogin_children_inherit_codex_environment(self):
        for login in (False, True):
            with self.subTest(login=login):
                shell = self.shell(login=login)
                output = shell.command("python3 -c 'import os; print(\"\\nCODEX_ENV=\"+os.environ.get(\"CODEX_TUI_DISABLE_KEYBOARD_ENHANCEMENT\",\"missing\"))'")
                self.assertIn(b'\r\nCODEX_ENV=1\r\n', output)

    def test_requested_aliases_are_exact_in_both_shell_types(self):
        for login in (False, True):
            shell = self.shell(login=login)
            for name, expected in ALIASES.items():
                command = "printf '\\nALIAS<%s>\\n' \"${BASH_ALIASES[" + name + "]}\""
                output = shell.command(command)
                self.assertIn(('\r\nALIAS<' + expected + '>\r\n').encode(), output)

    @unittest.skipUnless(shutil.which('starship'), 'Native Starship required')
    def test_real_ssh_starship_prompt_survives_login_bashrc_and_uses_requested_theme(self):
        if os.geteuid() == 0 and not INSTALLED_USER:
            self.skipTest('Root intentionally uses plain red PS1; installed acceptance tests ordinary users')
        for login in (False, True):
            for context in ({'ssh_connection': '192.0.2.1 54321 192.0.2.2 22'},
                            {'ssh_tty': '/dev/pts/99'}):
                with self.subTest(login=login, **context):
                    shell = self.shell(login=login, **context)
                    output = shell.command("printf '\\n'; declare -F starship_precmd; printf '\\nPROMPT_CONFIG<%s>\\n' \"$STARSHIP_CONFIG\"")
                    self.assertIn(b'\r\nstarship_precmd\r\n', output)
                    path = Path(pwd.getpwnam(INSTALLED_USER).pw_dir) / '.config/starship.toml' if INSTALLED_USER else TEMPLATE
                    self.assertIn(('\r\nPROMPT_CONFIG<' + str(path) + '>\r\n').encode(), output)
                    # Actual PTY prompt, not merely a variable declaration.
                    self.assertIn('󰜴'.encode(), shell.startup + output)
                    self.assertIn(''.encode(), shell.startup + output)
                    self.assertNotIn('󰣇'.encode(), shell.startup + output)
                    self.assertNotIn(b'DEBIAN_DEFAULT> ', shell.startup)

    def test_local_console_uses_plain_bash_even_with_stale_ssh_context(self):
        if os.geteuid() == 0 and not INSTALLED_USER:
            self.skipTest('Ordinary-user prompt tested outside root or with installed test account')
        contexts = (
            {},
            {'ssh_connection': '', 'ssh_tty': ''},
            {'term': 'linux'},
            {'term': 'linux', 'ssh_connection': '192.0.2.1 54321 192.0.2.2 22'},
            {'term': 'linux', 'ssh_tty': '/dev/pts/99'},
        )
        for login in (False, True):
            for context in contexts:
                with self.subTest(login=login, **context):
                    shell = self.shell(login=login, **context)
                    output = shell.command("printf '\\n'; declare -F starship_precmd || :")
                    self.assertNotIn(b'\r\nstarship_precmd\r\n', output)
                    self.assertNotIn('󰜴'.encode(), shell.startup + output)
                    self.assertNotIn(''.encode(), shell.startup + output)
                    username = INSTALLED_USER or pwd.getpwuid(os.geteuid()).pw_name
                    self.assertRegex(shell.startup, re.escape(username.encode()) +
                                     rb'@[^:\r\n]+:[^\r\n]*\$ ')

    @unittest.skipUnless(shutil.which('starship'), 'Native Starship required')
    def test_explicit_starship_config_is_respected(self):
        if INSTALLED_USER or os.geteuid() == 0:
            self.skipTest('Isolated ordinary-user custom config test')
        config = self.home / 'custom.toml'
        config.write_text('format = "CUSTOM_PROMPT> "\n')
        for login in (False, True):
            with self.subTest(login=login):
                shell = self.shell(login=login, custom_config=config, ssh_tty='/dev/pts/99')
                self.assertIn(b'CUSTOM_PROMPT> ', shell.startup)

    def test_root_prompt_is_red_without_starship(self):
        if os.geteuid() != 0:
            self.skipTest('Native root shell tested in Debian container/VM')
        for login in (False, True):
            for context in ({}, {'ssh_connection': '192.0.2.1 54321 192.0.2.2 22'},
                            {'ssh_tty': '/dev/pts/99'}):
                with self.subTest(login=login, **context):
                    shell = self.shell(login=login, root=True, **context)
                    output = shell.command("printf '\\nROOT_PS1<%s>\\n' \"$PS1\"; declare -F starship_precmd || :")
                    self.assertIn(rb'ROOT_PS1<\[\e[31m\]\u@\h:\w\$\[\e[0m\] >', output)
                    self.assertNotIn(b'\r\nstarship_precmd\r\n', output)
                    self.assertRegex(shell.startup, rb'\x1b\[31mroot@[^:\r\n]+:')

    def test_no_release_filters_are_installed(self):
        shell = self.shell()
        output = shell.command("bind -m emacs-standard -s; bind -m vi-insert -s; bind -m vi-command -s")
        for modifier in (5, 69, 133, 197):
            self.assertNotIn(f'100;{modifier}:3u'.encode(), output)

    def test_normal_arrow_editing_and_literal_tail_are_preserved(self):
        shell = self.shell()
        shell.command("bind 'set enable-bracketed-paste off'; _capture() { printf '\\nCAPTURE<%s>\\n' \"$READLINE_LINE\"; READLINE_LINE=; READLINE_POINT=0; }; bind -x '\"\\C-g\":_capture'")
        os.write(shell.fd, b'ac\x1b[Db\x07')
        self.assertIn(b'\r\nCAPTURE<abc>\r\n', shell.until(b'CAPTURE<abc>\r\n'))
        os.write(shell.fd, b'0;133:3u\x07')
        self.assertIn(b'CAPTURE<0;133:3u>\r\n', shell.until(b'CAPTURE<0;133:3u>\r\n'))

    def test_ordinary_ctrl_d_exits_empty_prompt(self):
        shell = self.shell()
        shell.command("PROMPT_COMMAND=; PS1='EOF_READY> '")
        shell.until(b'EOF_READY> ')
        os.write(shell.fd, b'\x04')
        for _ in range(120):
            pid, status = os.waitpid(shell.pid, os.WNOHANG)
            if pid:
                shell.reaped = True
                self.assertEqual(os.waitstatus_to_exitcode(status), 0)
                break
            time.sleep(.025)
        else:
            self.fail('Ctrl+D did not exit the empty prompt')

    def test_noninteractive_and_other_shells_are_quiet(self):
        for binary in (BASH, shutil.which('dash')):
            if not binary:
                continue
            for context in ({}, {'SSH_CONNECTION': '192.0.2.1 54321 192.0.2.2 22'},
                            {'SSH_TTY': '/dev/pts/99', 'TERM': 'linux'}):
                with self.subTest(binary=binary, **context):
                    result = subprocess.run([binary, '-c', '. ' + shlex.quote(str(SHARED))],
                                            env=env(self.home) | context, capture_output=True)
                    self.assertEqual((result.returncode, result.stdout, result.stderr), (0, b'', b''))


if __name__ == '__main__':
    if INSTALLED_USER:
        account = pwd.getpwnam(INSTALLED_USER)
        if os.geteuid() != 0 or account.pw_uid < 1000 or Path(account.pw_shell).resolve() != Path(BASH).resolve():
            raise SystemExit('Installed acceptance requires root and a regular Bash test account')
        if (Path(account.pw_dir) / '.inputrc').exists():
            raise SystemExit('Installed acceptance requires no personal inputrc')
        if Path('/etc/profile.d/rougarou-readline.sh').exists():
            raise SystemExit('Obsolete packaged Readline filters remain active')
        sys.argv = [sys.argv[0]]
    unittest.main()
