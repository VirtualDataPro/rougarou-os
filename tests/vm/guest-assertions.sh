#!/bin/sh
# TEST ONLY: executed via QGA in disposable acceptance VMs.
set -eu
printf '=== operating system ===\n'
cat /etc/os-release
uname -r
test "$(. /etc/os-release; printf %s "$ID")" = rougarou
test -s /etc/machine-id
test -s /etc/ssh/ssh_host_ed25519_key
test "$(systemctl get-default)" = multi-user.target
for service in ssh qemu-guest-agent; do
    tries=0
    while [ "$(systemctl is-active "$service")" != active ] && [ "$tries" -lt 30 ]; do
        sleep 1
        tries=$((tries + 1))
    done
    test "$(systemctl is-active "$service")" = active
done
test ! -e /usr/bin/Hyprland
test ! -e /usr/bin/Xorg
test ! -e /home/tester/.codex/auth.json
test ! -e /home/tester/.config/gh/hosts.yml
printf '=== package ownership ===\n'
test -z "$(dpkg --audit)"
dpkg-query -W rougarou-base
dpkg-query -S /usr/local/bin/rougarou /usr/local/bin/bat /etc/issue
python3 - <<'PY'
import json, subprocess
from pathlib import Path
expected = json.loads('@PACKAGE_VERSIONS_JSON@')
selected = json.loads('@SOFTWARE_CHOICES_JSON@')
recorded = dict(line.split('=', 1) for line in Path('/etc/rougarou/install-software').read_text().splitlines())
assert recorded == selected, (recorded, selected)
for package, version in expected.items():
    actual = subprocess.check_output(['dpkg-query', '-W', '-f=${Version}', package], text=True)
    assert actual == version, (package, actual, version)
for package in ('rougarou-codex', 'rougarou-herdr', 'rougarou-gemini', 'rougarou-opencode', 'claude-code'):
    if package in expected:
        continue
    status = subprocess.run(['dpkg-query', '-W', '-f=${Status}', package], text=True, capture_output=True)
    assert status.stdout != 'install ok installed', ('Unselected agent installed', package)
for package, wanted in (('docker.io', selected['docker'] != 'none'),
                        ('docker-cli', selected['docker'] != 'none'), ('podman', selected['podman'] == 'true')):
    status = subprocess.run(['dpkg-query', '-W', '-f=${Status}', package], text=True, capture_output=True)
    assert (status.stdout == 'install ok installed') == wanted, (package, wanted, status.stdout)
assert not Path('/home/tester/.local/bin/claude').exists()
assert not Path('/home/tester/.local/bin/opencode').exists()
print('EXACT_RECORDED_SELECTION_AND_UNSELECTED_PACKAGE_ABSENCE_PASSED')
print('EXACT_CUSTOM_PACKAGE_VERSIONS_PASSED')
PY
cmp /etc/issue /usr/share/rougarou/issue
cmp /etc/motd /usr/share/rougarou/motd
verification_home=$(mktemp -d)
gpg --batch --homedir "$verification_home" --no-default-keyring --keyring /usr/share/keyrings/rougarou-archive-keyring.gpg --verify /var/cache/rougarou/repo/InRelease
rm -rf "$verification_home"
printf '=== operator boundary ===\n'
id tester
! id -nG tester | tr ' ' '\n' | grep -qx docker
runuser -u tester -- /usr/local/bin/rougarou version
test "$(runuser -u tester -- /usr/local/bin/rougarou version)" = 'Rougarou OS @VERSION@'
runuser -u tester -- /usr/local/bin/rougarou access --json
runuser -u tester -- /usr/local/bin/rougarou access --json | python3 -c 'import json,sys; assert json.load(sys.stdin)["mode"] == "@ACCESS@"'
test "$(runuser -u tester -- id -u)" = 1000
if [ '@ACCESS@' = headless ]; then
    runuser -u tester -- sudo -n -k -- /usr/bin/true
    test -f /etc/sudoers.d/90-rougarou-owner
else
    if runuser -u tester -- sudo -n -k -- /usr/bin/true; then echo 'UNEXPECTED PASSWORDLESS ROOT'; exit 1; fi
    test ! -e /etc/sudoers.d/90-rougarou-owner
fi
runuser -u tester -- /usr/local/bin/rougarou doctor
if [ '@AGENT@' = codex ]; then
    runuser -u tester -- codex --version
    runuser -u tester -- codex --help | grep -F -- --approve-for-me
    runuser -u tester -- codex --help | grep -F -- --no-alt-screen
fi
printf '=== server tools and initial container policy ===\n'
python3 - <<'PY'
from pathlib import Path
import json, shutil, subprocess, tomllib
selected = json.loads('@SOFTWARE_CHOICES_JSON@')
commands = {
    'starship': '--version', 'ncdu': '--version',
    'eza': '--version', 'fzf': '--version', 'dig': '-v', 'host': '-V',
    'nslookup': '-version', 'named-checkconf': '-v', 'mtr': '--version',
    'bat': '--version',
}
if selected['docker'] != 'none': commands['docker'] = '--version'
if selected['podman'] == 'true': commands['podman'] = '--version'
if selected['herdr'] == 'true': commands['herdr'] = '--version'
if selected['agent'] == 'gemini': commands['gemini'] = '--version'
for name, argument in commands.items():
    assert shutil.which(name), name
    result = subprocess.run(['runuser', '-u', 'tester', '--', name, argument],
                            text=True, capture_output=True, check=True, timeout=10)
    print(name + ': ' + (result.stdout + result.stderr).strip().replace('\n', ' | '))
for home in ('/root', '/home/tester', '/etc/skel'):
    config = tomllib.loads((Path(home) / '.config/herdr/config.toml').read_text())
    assert config['update']['version_check'] is False
    assert config['update']['manifest_check'] is False
    bashrc = (Path(home) / '.bashrc').read_text()
    assert bashrc.count('# >>> Rougarou shell defaults >>>') == 1
    assert 'export CODEX_TUI_DISABLE_KEYBOARD_ENHANCEMENT=1' in bashrc
    assert (Path(home) / '.config/starship.toml').read_bytes() == Path('/usr/share/rougarou/starship.toml').read_bytes()
for entry in Path('/proc').iterdir():
    if entry.name.isdigit():
        try:
            assert (entry / 'comm').read_text().strip() != 'herdr', 'Herdr must not autostart'
        except FileNotFoundError:
            pass
print('SERVER_TOOLS_AND_HERDR_DEFAULTS_PASSED')
PY
case '@DOCKER@' in
  rootless)
    for unit in docker.service docker.socket containerd.service; do
      test "$(systemctl is-enabled "$unit" || true)" = masked
      test "$(systemctl is-active "$unit" || true)" = inactive
    done ;;
  none)
    for unit in docker.service docker.socket containerd.service; do
      test "$(systemctl show "$unit" -p LoadState --value)" = not-found
      test ! -e "/etc/systemd/system/$unit"
      test ! -L "/etc/systemd/system/$unit"
    done ;;
  system)
    for unit in docker.service docker.socket containerd.service; do
      test "$(systemctl is-enabled "$unit")" = enabled
      tries=0
      while [ "$(systemctl is-active "$unit" || true)" != active ] && [ "$tries" -lt 30 ]; do
        sleep 1; tries=$((tries + 1))
      done
      test "$(systemctl is-active "$unit")" = active
    done ;;
esac
printf '=== install hook ===\n'
tail -n 30 /var/log/rougarou-install.log
grep -q 'Rougarou OS installation completed.' /var/log/rougarou-install.log
python3 - <<'PY'
import base64
for label, source in [('timing', '@TIMING_TEST_BASE64@'), ('grub', '@GRUB_TEST_BASE64@')]:
    exec(compile(base64.b64decode(source), label + '-acceptance.py', 'exec'), {'__name__': '__main__'})
PY
printf '=== first login ===\n'
python3 - <<'PY'
import errno, json, os, pty, select, signal, time
from pathlib import Path
config_path = Path('/home/tester/.config/rougarou/config.json')
already_seen = config_path.exists() and json.loads(config_path.read_text()).get('first_login_seen', False)
pid, terminal = pty.fork()
if pid == 0:
    os.environ.pop('ROUGAROU_WELCOME_SHOWN', None)
    os.execl('/usr/sbin/runuser', 'runuser', '--login', 'tester')
transcript = b''
deadline = time.monotonic() + 30
try:
    if not already_seen:
        while b'Configure this operator now?' not in transcript:
            if time.monotonic() > deadline:
                raise RuntimeError('First-login invitation did not appear')
            if select.select([terminal], [], [], 1)[0]:
                transcript += os.read(terminal, 65536)
        os.write(terminal, b'n\n')
        while b'Resume whenever you like' not in transcript:
            if time.monotonic() > deadline:
                raise RuntimeError('First-login skip did not return')
            if select.select([terminal], [], [], 1)[0]:
                transcript += os.read(terminal, 65536)
    os.write(terminal, b'rougarou version\nexit\n')
    while True:
        if time.monotonic() > deadline:
            raise RuntimeError('Operator shell did not exit')
        if select.select([terminal], [], [], 1)[0]:
            try:
                block = os.read(terminal, 65536)
            except OSError as error:
                if error.errno == errno.EIO:
                    break
                raise
            if not block:
                break
            transcript += block
    os.waitpid(pid, 0)
    config = json.load(open('/home/tester/.config/rougarou/config.json'))
    assert config['first_login_seen'] is True
    assert not config.get('setup_complete')
    assert b'Rougarou OS 0.1.0' in transcript
    assert transcript.count(b'ROUGAROU OS') == 1, 'Login must print exactly one welcome banner'
    if already_seen:
        assert b'Configure this operator now?' not in transcript
    print('LOGIN_SINGLE_BANNER_AND_SHELL_PASSED' if already_seen else 'FIRST_LOGIN_SKIP_SINGLE_BANNER_AND_SHELL_PASSED')
finally:
    os.close(terminal)
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
PY
printf '=== installed Bash defaults ===\n'
python3 - <<'PY'
import base64, subprocess, tempfile
source = base64.b64decode('@SHELL_TEST_BASE64@')
with tempfile.NamedTemporaryFile(suffix='.py') as script:
    script.write(source)
    script.flush()
    subprocess.run(['/usr/bin/python3', script.name, '--installed', 'tester'], check=True)
print('INSTALLED_LOGIN_NONLOGIN_SHELL_DEFAULTS_PASSED')
if '@AGENT@' == 'codex':
    source = base64.b64decode('@CODEX_KEYBOARD_TEST_BASE64@')
    with tempfile.NamedTemporaryFile(suffix='.py') as script:
        script.write(source)
        script.flush()
        subprocess.run(['/usr/bin/python3', script.name], check=True)
    print('CODEX_KEYBOARD_ENHANCEMENT_DISABLE_PASSED')
if '@HERDR@' == 'true':
    source = base64.b64decode('@HERDR_TEST_BASE64@')
    with tempfile.NamedTemporaryFile(suffix='.py', dir='/tmp') as script:
        import os
        script.write(source)
        script.flush()
        os.fchmod(script.fileno(), 0o644)
        subprocess.run(['runuser', '-u', 'tester', '--', '/usr/bin/python3', script.name], check=True, timeout=60)
    print('HERDR_NAMED_SERVER_START_STATUS_STOP_PASSED')
PY
printf '=== managed worker ===\n'
loginctl enable-linger tester
systemctl start user@1000.service
printf '=== installed operator tools ===\n'
python3 - <<'PYTOOLS'
import base64, os, subprocess, tempfile
source = base64.b64decode('@OPERATOR_FEATURES_TEST_BASE64@')
with tempfile.NamedTemporaryFile(suffix='.py', dir='/tmp') as script:
    script.write(source)
    script.flush()
    os.fchmod(script.fileno(), 0o644)
    subprocess.run(['runuser', '-u', 'tester', '--', 'env', 'XDG_RUNTIME_DIR=/run/user/1000',
                    '/usr/bin/python3', script.name], check=True, timeout=180)
print('INSTALLED_UPDATE_BACKUP_NOTIFICATION_MENU_ACCEPTANCE_PASSED')
PYTOOLS
printf '=== rootless offline containers ===\n'
if [ '@DOCKER@' = rootless ]; then
python3 - <<'PY'
import base64, os, subprocess, tempfile
source = base64.b64decode('@ROOTLESS_TEST_BASE64@')
with tempfile.NamedTemporaryFile(suffix='.py', dir='/tmp') as script:
    script.write(source)
    script.flush()
    os.fchmod(script.fileno(), 0o644)
    command = ['runuser', '-u', 'tester', '--', 'env', 'XDG_RUNTIME_DIR=/run/user/1000',
               '/usr/bin/python3', script.name]
    if '@PODMAN@' != 'true':
        command.append('--docker-only')
    subprocess.run(command, check=True, timeout=150)
subprocess.run(['runuser', '-u', 'tester', '--', 'env', 'XDG_RUNTIME_DIR=/run/user/1000',
                'rougarou-docker', 'stop'], check=True, timeout=30)
print('ROOTLESS_DOCKER_AND_PODMAN_OFFLINE_CONTAINERS_PASSED' if '@PODMAN@' == 'true'
      else 'ROOTLESS_DOCKER_WITHOUT_PODMAN_OFFLINE_CONTAINER_PASSED')
PY
fi
if [ '@DOCKER@' = system ]; then
python3 - <<'PY'
import base64
exec(compile(base64.b64decode('@SYSTEM_DOCKER_TEST_BASE64@'), 'system-docker-acceptance.py', 'exec'), {'__name__': '__main__'})
PY
fi
runuser -u tester -- env XDG_RUNTIME_DIR=/run/user/1000 /usr/local/bin/rougarou service enable
runuser -u tester -- env XDG_RUNTIME_DIR=/run/user/1000 python3 - <<'PY'
import json, subprocess, time

def command(*args):
    return subprocess.run(['/usr/local/bin/rougarou', *args], text=True, capture_output=True, check=True).stdout.strip()

def completed(job_id):
    for _ in range(40):
        job = json.loads(command('jobs', 'show', job_id, '--json'))
        if job['state'] in {'succeeded', 'failed', 'timed_out', 'interrupted'}:
            return job
        time.sleep(.25)
    raise RuntimeError('Test worker did not finish its command')

success = command('jobs', 'run', '--workspace', '/home/tester/Work', '--timeout', '20', '--', '/usr/bin/python3', '-c', 'import os; assert os.geteuid() == 1000; print("WORKER_UNPRIVILEGED_OK")')
assert completed(success)['state'] == 'succeeded'
assert 'WORKER_UNPRIVILEGED_OK' in command('jobs', 'logs', success)
denied = command('jobs', 'run', '--workspace', '/home/tester/Work', '--timeout', '20', '--', '/usr/bin/sudo', '-n', '-k', '--', '/usr/bin/true')
assert completed(denied)['state'] == 'failed'
assert json.loads(command('status', '--json'))['healthy'] is True
print('WORKER_COMMAND_LIFETIME_AND_NO_NEW_PRIVILEGES_PASSED')
command('service', 'disable')
PY
python3 - <<'PY'
import hashlib, json
from pathlib import Path
print('IDENTITY_FACTS=' + json.dumps({
    'machine': hashlib.sha256(Path('/etc/machine-id').read_bytes()).hexdigest(),
    'ssh_public': hashlib.sha256(Path('/etc/ssh/ssh_host_ed25519_key.pub').read_bytes()).hexdigest(),
}))
PY
printf 'ROUGAROU_VM_ASSERTIONS_PASSED\n'
