"""Explicit, signed APT updates with immutable plans and honest recovery records."""
from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import email.utils
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import uuid
from urllib.parse import urlsplit

HELPER = '/usr/lib/rougarou-system/update'
KEY = '/usr/share/keyrings/rougarou-archive-keyring.gpg'
DEBIAN_KEY = '/usr/share/keyrings/debian-archive-keyring.gpg'
STATE = Path('/var/lib/rougarou/updates')
MAX_AGE = 24 * 3600
ENV = {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LC_ALL': 'C',
       'DEBIAN_FRONTEND': 'noninteractive', 'NEEDRESTART_MODE': 'l'}


class UpdateError(Exception):
    def __init__(self, message, code='unavailable'):
        super().__init__(message)
        self.code = code


def require(condition, message, code='invalid'):
    if not condition:
        raise UpdateError(message, code)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()


def file_hash(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(chunk)
    return result.hexdigest()


def run(argv, *, environment=None, timeout=60, log=None):
    try:
        result = subprocess.run(argv, env=environment or ENV, stdin=subprocess.DEVNULL,
                                capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired as error:
        if log is not None:
            Path(log).write_bytes((error.stdout or b'') + (error.stderr or b'') + b'\nCOMMAND TIMED OUT\n')
            Path(log).chmod(0o600)
        raise UpdateError(f'{Path(argv[0]).name} exceeded its time limit; inspect the update state.', 'command_timeout') from None
    if log is not None:
        Path(log).write_bytes(result.stdout + result.stderr + f'\nEXIT STATUS: {result.returncode}\n'.encode())
        Path(log).chmod(0o600)
    require(result.returncode == 0, f'{Path(argv[0]).name} failed; inspect {log}.' if log is not None else f'{Path(argv[0]).name} failed; run rougarou doctor.', 'command_failed')
    return result.stdout.decode('utf-8')


def safe_file(path, *, directory=False):
    info = Path(path).lstat()
    require((stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))
            and info.st_uid == 0 and not info.st_mode & 0o022,
            'Update configuration/cache has unsafe ownership or permissions.', 'unsafe_path')


def safe_key(path):
    # Debian's packaged .gpg keyring may be a root-owned symlink to .pgp.
    path = Path(path)
    info = path.lstat()
    require(info.st_uid == 0, 'Signing key link is not administrator-owned.', 'unsafe_path')
    resolved = path.resolve(strict=True)
    for parent in resolved.parents:
        safe_file(parent, directory=True)
    safe_file(resolved)


def paragraphs(data):
    rows, row, previous = [], {}, None
    for line in data.splitlines() + ['']:
        if not line.strip():
            if row:
                rows.append(row)
            row, previous = {}, None
        elif line[0].isspace():
            require(previous is not None, 'Invalid continued metadata field.')
            row[previous] += '\n' + line[1:]
        else:
            key, sep, value = line.partition(':')
            require(sep and key not in row, 'Invalid or duplicate metadata field.')
            row[key], previous = value.strip(), key
    return rows


def source_policy(etc=Path('/etc/apt')):
    """Conservative supported subset: no trust overrides, custom key paths or vendors."""
    rows, inputs = [], {}
    preferences = [etc / 'preferences'] + list((etc / 'preferences.d').glob('*'))
    for path in preferences:
        if path.is_file():
            safe_file(path)
            require(not any(line.strip() and not line.lstrip().startswith('#') for line in path.read_text().splitlines()),
                    'Custom APT preferences require manual review; the guided updater will not ignore them.', 'policy_error')
    files = ([etc / 'sources.list'] if (etc / 'sources.list').exists() else [])
    directory = etc / 'sources.list.d'
    if directory.exists():
        safe_file(directory, directory=True)
        files += sorted(p for p in directory.iterdir() if p.suffix in {'.sources', '.list'})
    for path in files:
        safe_file(path)
        text = path.read_text()
        inputs[str(path)] = digest(text.encode())
        text = '\n'.join(line for line in text.splitlines() if not line.lstrip().startswith('#'))
        if path.suffix == '.sources':
            entries = paragraphs(text)
        else:
            entries = []
            for line in text.splitlines():
                if not line.strip():
                    continue
                match = re.fullmatch(r'deb \[signed-by=([^\] ]+)\] (\S+) (\S+)(?: (.+))?', line.strip())
                require(match is not None, 'Unsupported enabled APT source; use signed deb822 entries.', 'policy_error')
                key, uri, suite, components = match.groups()
                entries.append({'Types': 'deb', 'URIs': uri, 'Suites': suite,
                                'Signed-By': key, 'Components': components or ''})
        for entry in entries:
            if entry.get('Enabled', 'yes').lower() == 'no':
                continue
            require(not set(entry) - {'Types', 'URIs', 'Suites', 'Components', 'Signed-By', 'Architectures', 'Enabled'},
                    'APT source has unsupported options or authentication overrides.', 'policy_error')
            require(entry.get('Types') == 'deb' and entry.get('Enabled', 'yes') == 'yes'
                    and entry.get('Architectures', 'amd64') == 'amd64', 'Unsupported enabled APT source.', 'policy_error')
            uri, suite, key = (entry.get(field, '') for field in ('URIs', 'Suites', 'Signed-By'))
            require(len(uri.split()) == len(suite.split()) == len(key.split()) == 1,
                    'Use one URI, suite and signing key per source.', 'policy_error')
            parts = urlsplit(uri)
            require(not (parts.username or parts.password or parts.query or parts.fragment)
                    and not any(c.isspace() or ord(c) < 32 for c in uri), 'Source URL contains unsupported private/options fields.', 'policy_error')
            components = entry.get('Components', '').split()
            if key == KEY and suite == './' and not components:
                if parts.scheme == 'file' and not parts.netloc and parts.path.rstrip('/') == '/var/cache/rougarou/repo':
                    channel = 'bootstrap'
                else:
                    match = re.fullmatch(r'.*/channels/(testing|stable)/?', parts.path)
                    require(parts.scheme in {'https', 'http'} and parts.hostname and match,
                            'Rougarou updates must use a configured testing or stable channel.', 'policy_error')
                    channel = match[1]
            elif (key == DEBIAN_KEY and uri.rstrip('/') == 'https://security.debian.org/debian-security'
                  and suite == 'trixie-security' and components and set(components) <= {'main', 'contrib', 'non-free', 'non-free-firmware'}):
                channel = 'debian-security'
            else:
                raise UpdateError('Enabled APT source bypasses Rougarou promotion or direct Debian security policy.', 'policy_error')
            safe_key(key)
            rows.append({'name': path.name, 'uri': uri.rstrip('/'), 'suite': suite,
                         'components': components, 'key': key, 'key_sha256': file_hash(key), 'channel': channel})
    require(rows, 'No supported signed APT sources are configured.', 'policy_error')
    require(len(rows) <= 16, 'Too many enabled APT sources.', 'policy_error')
    require(len({(r['uri'], r['suite']) for r in rows}) == len(rows), 'Duplicate enabled APT source.', 'policy_error')
    return {'sources': rows, 'inputs': inputs}


def source_text(sources):
    return '\n\n'.join('\n'.join(['Types: deb', f"URIs: {s['uri']}", f"Suites: {s['suite']}",
        *(['Components: ' + ' '.join(s['components'])] if s['components'] else []),
        'Architectures: amd64', f"Signed-By: {s['key']}"]) for s in sources) + '\n'


def apt_config(directory, sources, *, install_hook=None):
    """This config is loaded first through APT_CONFIG, excluding ambient hooks/options."""
    directory = Path(directory)
    config = directory / 'apt.conf'
    entries = {'Dir::Etc::main': '/dev/null', 'Dir::Etc::parts': '-',
        'Dir::Etc::sourcelist': str(sources), 'Dir::Etc::sourceparts': '-',
        'Dir::Etc::preferences': '/dev/null', 'Dir::Etc::preferencesparts': '-',
        'Dir::Etc::netrc': '/dev/null', 'Dir::Etc::netrcparts': '-',
        'Dir::State::lists': str(directory / 'lists'), 'Dir::Cache::archives': str(directory / 'archives'),
        'Dir::Cache::pkgcache': '', 'Dir::Cache::srcpkgcache': '',
        'Acquire::AllowInsecureRepositories': 'false', 'Acquire::AllowDowngradeToInsecureRepositories': 'false',
        'Acquire::Check-Valid-Until': 'true', 'Acquire::Check-Date': 'true',
        'Acquire::Languages': 'none', 'APT::Update::Error-Mode': 'any',
        'APT::Get::AllowUnauthenticated': 'false', 'APT::Install-Recommends': 'false',
        'Acquire::http::Timeout': '30', 'Acquire::https::Timeout': '30',
        'DPkg::Lock::Timeout': '0'}
    config.write_text(''.join(f'{key} "{value}";\n' for key, value in entries.items())
                      + (f'DPkg::Pre-Invoke {{ "{install_hook}"; }};\n' if install_hook else ''))
    return dict(ENV, APT_CONFIG=str(config))


def signature(path, key, timeout=4):
    safe_file(path)
    safe_key(key)
    require(Path(path).stat().st_size <= 2 * 1024 * 1024, 'Signed repository metadata is too large.')
    # sqv verifies the configured key only, without a mutable GPG home/trust DB.
    with tempfile.TemporaryDirectory(prefix='rougarou-signature-', dir='/tmp') as scratch:
        output = Path(scratch) / 'authenticated'
        try:
            run(['/usr/bin/sqv', '--keyring', key, '--cleartext', '--output', str(output), str(path)], timeout=timeout)
        except UpdateError as error:
            if error.code == 'command_timeout':
                raise UpdateError('Local signature check timed out.', 'unavailable') from error
            raise UpdateError('Repository signature verification failed.', 'invalid_signature') from error
        fields = paragraphs(output.read_text())
        require(len(fields) == 1, 'Malformed signed repository metadata.', 'invalid_metadata')
        return fields[0]


def release_check(fields, source, now=None):
    now = time.time() if now is None else now
    try:
        date = email.utils.parsedate_to_datetime(fields['Date']).timestamp()
        expiry = email.utils.parsedate_to_datetime(fields['Valid-Until']).timestamp()
    except (KeyError, TypeError, ValueError, OverflowError):
        raise UpdateError('Repository lacks valid Date/Valid-Until metadata.', 'invalid_metadata') from None
    require(date <= now + 300 and expiry > date, 'Repository time metadata is invalid.', 'invalid_metadata')
    require(expiry > now, 'Repository metadata expired; run rougarou update refresh.', 'expired')
    if source['channel'] == 'debian-security':
        require(fields.get('Origin') == 'Debian' and fields.get('Label') == 'Debian-Security'
                and fields.get('Codename') == 'trixie-security', 'Security archive identity differs.', 'invalid_metadata')
    else:
        require(fields.get('Origin') == fields.get('Label') == 'Rougarou'
                and fields.get('Codename') == 'rougarou', 'Rougarou archive identity differs.', 'invalid_metadata')
        if source['channel'] != 'bootstrap':
            require(fields.get('Suite') == source['channel'], 'Signed channel does not match configured channel.', 'invalid_metadata')
    return expiry


def signed_hashes(fields):
    result = {}
    for line in fields.get('SHA256', '').splitlines():
        if not line.strip():
            continue
        values = line.split()
        require(len(values) == 3 and re.fullmatch('[0-9a-f]{64}', values[0]) and values[1].isdigit(), 'Invalid signed SHA256 entry.')
        require(values[2] not in result, 'Duplicate signed SHA256 path.')
        result[values[2]] = (values[0], int(values[1]))
    require(result, 'Signed repository has no SHA256 metadata.')
    return result


def inventory():
    require(not run(['/usr/bin/dpkg', '--print-foreign-architectures']).strip(), 'Foreign package architectures need manual administrator review.')
    rows = {}
    output = run(['/usr/bin/dpkg-query', '-W', '-f=${Package}\t${Version}\t${Architecture}\t${db:Status-Status}\t${db:Status-Want}\n'])
    for line in output.splitlines():
        name, version, architecture, status, want = line.split('\t')
        rows[name] = {'version': version, 'architecture': architecture, 'status': status, 'want': want}
    return rows


def auto_packages():
    path = Path('/var/lib/apt/extended_states')
    if not path.exists():
        return []
    installed = inventory()
    return sorted({row['Package'] for row in paragraphs(path.read_text())
                   if row.get('Auto-Installed') == '1' and row.get('Package') in installed
                   and installed[row['Package']]['status'] == 'installed'})


def machine():
    value = Path('/etc/machine-id').read_text().strip()
    require(re.fullmatch('[a-f0-9]{32}', value), 'This host has no valid machine identity.')
    return digest(value.encode())


def operations(output):
    result = {}
    configured = []
    for line in output.splitlines():
        require(not line.startswith(('Remv ', 'Purg ')), 'APT proposed a removal; manual review is required.')
        if line.startswith('Inst '):
            match = re.match(r'^Inst ([a-z0-9][a-z0-9+.-]*)(?: \[[^]]+\])? \(([^ ]+)', line)
            require(match is not None, 'Unsupported APT plan line.')
            require(match[1] not in result, 'Duplicate APT package operation.')
            result[match[1]] = match[2]
        elif line.startswith('Conf '):
            configured.append(line.split()[1])
    require(set(configured) <= result.keys(), 'APT proposed unrelated package configuration.')
    return result


def write_json(path, data, mode=0o600):
    path = Path(path)
    temporary = path.with_name('.' + path.name + '.' + uuid.uuid4().hex)
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            os.fchmod(stream.fileno(), mode)
            stream.write(canonical(data) + b'\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        parent_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(parent_fd)
        finally:
            os.close(parent_fd)
    finally:
        temporary.unlink(missing_ok=True)


class Updates:
    """Paths are injectable for isolated tests, never accepted as CLI root overrides."""
    def __init__(self, state=STATE, etc=Path('/etc/apt')):
        self.state, self.etc = Path(state), Path(etc)

    @contextlib.contextmanager
    def lock(self):
        require(os.geteuid() == 0, 'This operation requires administrator authentication.')
        if not self.state.parent.exists():
            self.state.parent.mkdir(parents=True, mode=0o755)
            self.state.parent.chmod(0o755)
        self.state.mkdir(mode=0o755, exist_ok=True)
        safe_file(self.state, directory=True)
        self.state.chmod(0o755)
        fd = os.open(self.state / '.lock', os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            yield
        except BlockingIOError:
            raise UpdateError('Another guided update is active.', 'busy') from None
        finally:
            os.close(fd)

    def current(self):
        pointer = self.state / 'current.json'
        require(pointer.exists(), 'No guided metadata is cached. Run rougarou update refresh.', 'missing')
        safe_file(pointer)
        value = json.loads(pointer.read_text())
        require(re.fullmatch('[a-f0-9]{32}', value.get('generation', '')), 'Invalid metadata generation.')
        root = self.state / 'metadata' / value['generation']
        safe_file(root, directory=True)
        safe_file(root / 'receipt.json')
        return root, json.loads((root / 'receipt.json').read_text())

    def refresh(self):
        with self.lock():
            policy = source_policy(self.etc)
            generation = uuid.uuid4().hex
            root = self.state / 'metadata' / generation
            root.parent.mkdir(mode=0o755, exist_ok=True)
            safe_file(root.parent, directory=True)
            root.parent.chmod(0o755)
            root.mkdir(mode=0o755)
            root.chmod(0o755)
            receipt = {'schema_version': 1, 'generation': generation, 'policy': policy,
                       'refreshed_at': time.time(), 'sources': []}
            (root / 'lists').mkdir()
            (root / 'lists/partial').mkdir()
            (root / 'archives/partial').mkdir(parents=True)
            for number, source in enumerate(policy['sources']):
                work = root / str(number)
                work.mkdir()
                (work / 'lists/partial').mkdir(parents=True)
                (work / 'archives/partial').mkdir(parents=True)
                (work / 'source.sources').write_text(source_text([source]))
                environment = apt_config(work, work / 'source.sources')
                run(['/usr/bin/apt-get', 'update'], environment=environment, timeout=600, log=work / 'refresh.log')
                releases = list((work / 'lists').glob('*_InRelease'))
                require(len(releases) == 1, 'Expected one authenticated InRelease per source.')
                release = releases[0]
                fields = signature(release, source['key'])
                expiry = release_check(fields, source)
                hashes = signed_hashes(fields)
                indexes = []
                for target in paragraphs(run(['/usr/bin/apt-get', 'indextargets'], environment=environment)):
                    if target.get('Identifier') != 'Packages':
                        continue
                    require(target.get('Trusted') == 'yes', 'APT did not authenticate an index.')
                    path = Path(target['Filename'])
                    require(path.parent == work / 'lists', 'APT index escaped its isolated directory.')
                    data = run(['/usr/lib/apt/apt-helper', 'cat-file', str(path)]).encode()
                    require(hashes.get(target['MetaKey']) == (digest(data), len(data)), 'Package index differs from signed metadata.')
                    indexes.append({'filename': str(path.relative_to(root)), 'metakey': target['MetaKey'], 'sha256': digest(data)})
                require(indexes, 'Source contains no authenticated package indexes.')
                receipt['sources'].append({'source': source, 'release': str(release.relative_to(root)),
                    'release_sha256': file_hash(release), 'valid_until': expiry, 'indexes': indexes})
                for path in (work / 'lists').iterdir():
                    if path.is_file() and path.name != 'lock':
                        target = root / 'lists' / path.name
                        require(not target.exists() or file_hash(target) == file_hash(path), 'Conflicting metadata cache filename.')
                        shutil.copyfile(path, target)
            require(source_policy(self.etc) == policy, 'APT policy changed during refresh.')
            (root / 'sources.sources').write_text(source_text(policy['sources']))
            apt_config(root, root / 'sources.sources')
            receipt['apt_config_sha256'] = file_hash(root / 'apt.conf')
            # Public metadata only; logs stay root-private. Native APT creates
            # some root-only files, so only the verified regular indexes change mode.
            for path in root.rglob('*'):
                if path.is_file() and path.suffix != '.log':
                    path.chmod(0o644)
                elif path.is_dir():
                    path.chmod(0o755)
            write_json(root / 'receipt.json', receipt, 0o644)
            write_json(self.state / 'current.json', {'generation': generation}, 0o644)
            return {'refreshed': True, 'generation': generation, 'sources': len(receipt['sources'])}

    def verified(self):
        root, receipt = self.current()
        safe_file(root / 'apt.conf')
        require(file_hash(root / 'apt.conf') == receipt['apt_config_sha256'], 'Isolated APT configuration changed.')
        require(receipt['policy'] == source_policy(self.etc), 'APT policy or signing key changed; refresh metadata.', 'stale')
        now = time.time()
        require(0 <= now - receipt['refreshed_at'] <= MAX_AGE, 'Guided metadata is older than 24 hours; refresh it.', 'stale')
        records, expires = {}, receipt['refreshed_at'] + MAX_AGE
        for item in receipt['sources']:
            source = item['source']
            release = root / item['release']
            require(file_hash(release) == item['release_sha256'], 'Cached signed metadata changed.')
            fields = signature(release, source['key'])
            expires = min(expires, release_check(fields, source, now))
            hashes = signed_hashes(fields)
            # Both per-source and merged APT inputs must remain the verified bytes.
            require(file_hash(root / 'lists' / release.name) == file_hash(release), 'APT release cache changed.')
            for index in item['indexes']:
                path = root / index['filename']
                safe_file(path)
                data = run(['/usr/lib/apt/apt-helper', 'cat-file', str(path)]).encode()
                require(hashes.get(index['metakey']) == (digest(data), len(data)) and digest(data) == index['sha256'], 'Cached package index changed.')
                require(file_hash(root / 'lists' / path.name) == file_hash(path), 'APT package cache changed.')
                for record in paragraphs(data.decode()):
                    if record.get('Architecture') in {'all', 'amd64'}:
                        records.setdefault((record.get('Package'), record.get('Version')), []).append((record, source))
        require((root / 'sources.sources').read_text() == source_text(receipt['policy']['sources']), 'Normalized source configuration changed.')
        return root, receipt, records, expires

    def plan(self):
        root, receipt, records, expires = self.verified()
        require(not run(['/usr/bin/dpkg', '--audit']).strip(), 'A previous dpkg transaction is incomplete; inspect recovery first.')
        before = inventory()
        environment = dict(ENV, APT_CONFIG=str(root / 'apt.conf'))
        output = run(['/usr/bin/apt-get', '--simulate', '--with-new-pkgs', '--no-remove', 'upgrade'], environment=environment)
        changes = operations(output)
        packages = []
        for name, version in sorted(changes.items()):
            old = before.get(name)
            if old and old['status'] == 'installed':
                require(old['want'] != 'hold', 'APT proposed changing a held package.')
                run(['/usr/bin/dpkg', '--compare-versions', version, 'gt', old['version']])
            matches = records.get((name, version), [])
            require(matches, 'Planned package has no verified signed index record.')
            identities = {(record.get('SHA256'), record.get('Size'), record.get('Architecture')) for record, _ in matches}
            require(len(identities) == 1, 'Same package version has conflicting signed bytes across sources.')
            record, source = matches[0]
            require(re.fullmatch('[a-f0-9]{64}', record.get('SHA256', '')) and record.get('Size', '').isdigit(), 'Package lacks a valid SHA256/size.')
            filename = record.get('Filename', '')
            require(re.fullmatch(r'[a-zA-Z0-9_./+%~:-]+', filename) and not filename.startswith('/') and '..' not in filename.split('/'), 'Unsafe signed package filename.')
            packages.append({'name': name, 'old_version': old['version'] if old and old['status'] == 'installed' else None,
                'version': version, 'architecture': record['Architecture'], 'sha256': record['SHA256'], 'size': int(record['Size']),
                'source': source['name'], 'channel': source['channel']})
        result = {'schema_version': 1, 'generation': receipt['generation'], 'machine_id_sha256': machine(),
                  'inventory_sha256': digest(canonical(before)), 'automatic_sha256': digest(canonical(auto_packages())), 'policy_sha256': digest(canonical(receipt['policy'])),
                  'valid_until': expires, 'packages': packages, 'removals': [], 'downgrades': [],
                  'download_bytes': sum(p['size'] for p in packages),
                  'reboot_required_now': Path('/run/reboot-required').exists(),
                  'reboot_may_be_needed': any(p['name'].startswith(('linux-image', 'linux-base', 'libc6', 'systemd')) for p in packages)}
        result['plan_id'] = digest(canonical(result))
        return result

    def assert_before(self, identifier):
        require(re.fullmatch('[a-f0-9]{32}', identifier), 'Invalid transaction identifier.')
        path = self.state / 'transactions' / identifier / 'before.json'
        safe_file(path)
        before = json.loads(path.read_text())
        require(machine() == before['plan']['machine_id_sha256'] and inventory() == before['inventory'],
                'Installed state changed after review; no packages were unpacked.', 'stale')
        require(auto_packages() == before['automatic_before'], 'APT automatic-package marks changed after review.', 'stale')
        require(time.time() < before['plan']['valid_until'], 'Plan expired before package installation.', 'stale')
        require(digest(canonical(source_policy(self.etc))) == before['plan']['policy_sha256'], 'APT policy changed before unpacking.', 'stale')

    def apply(self, plan_id, recovery_point, confirm=False):
        require(confirm, 'Applying updates requires --confirm after reviewing the plan.')
        require(re.fullmatch('[a-f0-9]{64}', plan_id or ''), 'Provide the exact reviewed --plan ID.')
        require(isinstance(recovery_point, str) and 1 <= len(recovery_point) <= 512
                and all(32 <= ord(c) < 127 for c in recovery_point), 'Provide a concrete recovery-point reference without control characters.')
        with self.lock():
            plan = self.plan()
            require(plan['plan_id'] == plan_id, 'Reviewed plan changed; inspect a new plan before applying.', 'stale')
            require(plan['packages'], 'No package updates are available.')
            root, _ = self.current()
            identifier = uuid.uuid4().hex
            transaction = self.state / 'transactions' / identifier
            transaction.mkdir(parents=True, mode=0o700)
            transaction.parent.chmod(0o700)
            before = {'schema_version': 1, 'plan': plan, 'inventory': inventory(), 'automatic_before': auto_packages(), 'started_at': time.time(),
                      'recovery': {'reference': recovery_point, 'assurance': 'operator-attested; not independently verified by this guest'}}
            write_json(transaction / 'before.json', before)
            parent_fd = os.open(transaction.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(parent_fd)
            finally:
                os.close(parent_fd)
            result = {'transaction': identifier, 'plan_id': plan_id, 'status': 'started', 'started_at': before['started_at'],
                      'package_count': len(plan['packages']), 'rebooted': False,
                      'boot_id': Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
                      'reboot_may_be_needed': plan['reboot_may_be_needed']}
            write_json(self.state / 'status.json', result, 0o644)
            try:
                (transaction / 'lists/partial').mkdir(parents=True)
                (transaction / 'archives/partial').mkdir(parents=True)
                for path in (root / 'lists').iterdir():
                    if path.is_file() and path.name != 'lock':
                        shutil.copyfile(path, transaction / 'lists' / path.name)
                sources = transaction / 'sources.sources'
                sources.write_text((root / 'sources.sources').read_text())
                environment = apt_config(transaction, sources)
                exact = [p['name'] + '=' + p['version'] for p in plan['packages']]
                command = ['/usr/bin/apt-get', '--no-remove', '--no-install-recommends', 'install', *exact]
                expected = {p['name']: p['version'] for p in plan['packages']}
                require(operations(run(command[:1] + ['--simulate'] + command[1:], environment=environment)) == expected,
                        'Exact package simulation differs from the reviewed plan.')
                run(command[:1] + ['--download-only', '-y'] + command[1:], environment=environment, timeout=1800, log=transaction / 'download.log')
                archives = {}
                for path in (transaction / 'archives').glob('*.deb'):
                    metadata = run(['/usr/bin/dpkg-deb', '-f', str(path), 'Package', 'Version', 'Architecture']).splitlines()
                    values = dict(line.split(': ', 1) for line in metadata)
                    archives[(values['Package'], values['Version'], values['Architecture'])] = path
                local = []
                for package in plan['packages']:
                    key = (package['name'], package['version'], package['architecture'])
                    require(key in archives, 'A reviewed package was not downloaded.')
                    path = archives[key]
                    require(file_hash(path) == package['sha256'] and path.stat().st_size == package['size'], 'Downloaded package differs from signed planned bytes.')
                    local.append(str(path))
                require(len(archives) == len(local), 'APT downloaded unreviewed packages.')
                require(self.plan()['plan_id'] == plan_id, 'System or metadata changed during download.', 'stale')
                # Empty sources prevent moving-channel resolution. The canonical
                # archive filenames remain in APT's cache for --no-download.
                sources.write_text('')
                for path in (transaction / 'lists').iterdir():
                    if path.is_file():
                        path.unlink()
                environment = apt_config(transaction, sources, install_hook=f'{HELPER} assert-before {identifier}')
                command = ['/usr/bin/apt-get', '--no-remove', '--no-download', '--no-install-recommends',
                           '-o', 'Dpkg::Options::=--force-confold', 'install', *local]
                require(operations(run(command[:1] + ['--simulate'] + command[1:], environment=environment)) == expected,
                        'Verified local package simulation differs from the reviewed plan.')
                self.assert_before(identifier)
                run(command[:1] + ['-y'] + command[1:], environment=environment, timeout=None, log=transaction / 'install.log')
                automatic = sorted(set(before['automatic_before']) | {p['name'] for p in plan['packages'] if p['old_version'] is None})
                if automatic:
                    run(['/usr/bin/apt-mark', 'auto', *automatic], environment=environment,
                        log=transaction / 'automatic-marks.log')
                require(auto_packages() == automatic, 'Automatic-package marks differ after installation; inspect the transaction.')
                after = inventory()
                expected_inventory = dict(before['inventory'])
                for package in plan['packages']:
                    expected_inventory[package['name']] = {'version': package['version'], 'architecture': package['architecture'], 'status': 'installed', 'want': 'install'}
                require(after == expected_inventory, 'Post-update inventory differs from the reviewed transaction; inspect recovery.')
                require(not run(['/usr/bin/dpkg', '--audit']).strip(), 'Package configuration is incomplete; inspect recovery.')
                write_json(transaction / 'after.json', {'inventory': after, 'finished_at': time.time()})
                result.update(status='complete', finished_at=time.time(), reboot_required_now=Path('/run/reboot-required').exists())
            except BaseException as error:
                result.update(status='interrupted' if isinstance(error, KeyboardInterrupt) else 'failed', finished_at=time.time())
                with contextlib.suppress(Exception):
                    write_json(transaction / 'after.json', {'inventory': inventory(), 'finished_at': time.time()})
                raise
            finally:
                write_json(transaction / 'result.json', result)
                write_json(self.state / 'status.json', result, 0o644)
            return result


def repository_status(*, budget_seconds=3):
    """Bounded local signature/expiry checks. No network or persistent cache writes."""
    deadline = time.monotonic() + min(10, max(0.1, budget_seconds))
    result = {'sources': [], 'checked_at': dt.datetime.now(dt.timezone.utc).isoformat()}
    try:
        policy = source_policy()
    except (UpdateError, OSError, ValueError) as error:
        result['sources'].append({'name': 'APT policy', 'channel': None, 'status': 'policy_error', 'valid_until': None, 'expires_in_seconds': None})
        return result
    try:
        targets = paragraphs(run(['/usr/bin/apt-get', 'indextargets'], timeout=max(0.05, deadline - time.monotonic())))
    except (UpdateError, OSError, subprocess.TimeoutExpired):
        targets = []
    try:
        root, receipt = Updates().current()
        guided = receipt['sources'] if receipt['policy'] == policy else []
    except (UpdateError, OSError, ValueError):
        root, guided = STATE, []
    for source in policy['sources']:
        if source['channel'] == 'debian-security':
            continue
        item = {'name': source['name'], 'channel': source['channel'], 'status': 'missing', 'valid_until': None, 'expires_in_seconds': None}
        candidates = [root / row['release'] for row in guided if row['source'] == source]
        for target in targets:
            if (target.get('Identifier') == 'Packages' and target.get('Repo-URI', '').rstrip('/') == source['uri']
                    and Path(target.get('Sourcesentry', '').rsplit(':', 1)[0]).name == source['name']):
                filename = target.get('Filename', '')
                filename = re.sub(r'\.(?:lz4|gz|xz|bz2|zst)$', '', filename)
                suffix = target.get('MetaKey', '').replace('/', '_')
                if suffix and filename.endswith(suffix):
                    candidates.append(Path(filename[:-len(suffix)] + 'InRelease'))
        try:
            require(candidates, 'No cached signed metadata.', 'missing')
            # Prefer the most recent successfully refreshed copy when both caches exist.
            path = max(candidates, key=lambda p: p.stat().st_mtime)
            require(time.monotonic() < deadline, 'Repository check exceeded its local time budget.', 'unavailable')
            fields = signature(path, source['key'], timeout=max(0.05, deadline - time.monotonic()))
            expiry = release_check(fields, source)
            remaining = int(expiry - time.time())
            item.update(status='expiring' if remaining <= 7 * 86400 else 'valid',
                        valid_until=dt.datetime.fromtimestamp(expiry, dt.timezone.utc).isoformat(), expires_in_seconds=remaining)
        except UpdateError as error:
            item['status'] = error.code if error.code in {'missing', 'expired', 'unavailable'} else 'invalid'
        except subprocess.TimeoutExpired:
            item['status'] = 'unavailable'
        except (OSError, ValueError):
            item['status'] = 'invalid'
        result['sources'].append(item)
    return result


def reboot_status():
    """Read only the kernel boot ID, standard marker and sanitized public result.

    `required` is the system marker; `recommended` also covers core-package
    updates in this boot. A recommendation is not proof a reboot is mandatory.
    """
    boot_id = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    required = Path('/run/reboot-required').exists()
    result = {'required': required, 'recommended': required,
              'reason': 'system-marker' if required else 'none', 'boot_id': boot_id}
    if required:
        return result
    try:
        path = STATE / 'status.json'
        if path.exists():
            safe_file(path)
            latest = json.loads(path.read_text())
            if (latest.get('reboot_may_be_needed') is True and latest.get('status') == 'complete'
                    and latest.get('boot_id') == boot_id):
                result.update(recommended=True, reason='core-packages-updated-this-boot')
    except (UpdateError, OSError, ValueError):
        result['reason'] = 'status-unavailable'
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(prog='rougarou update', description=__doc__)
    commands = parser.add_subparsers(dest='action', required=True)
    for action in ('plan', 'refresh', 'status', 'recovery'):
        commands.add_parser(action).add_argument('--json', action='store_true')
    apply = commands.add_parser('apply')
    apply.add_argument('--plan', required=True, help='exact plan_id from the reviewed plan')
    apply.add_argument('--recovery-point', required=True, help='existing VM snapshot/backup reference; operator-attested')
    apply.add_argument('--confirm', action='store_true')
    apply.add_argument('--json', action='store_true')
    args = parser.parse_args(argv)
    arguments = list(argv if argv is not None else sys.argv[1:])
    if args.action in {'refresh', 'apply'} and os.geteuid() != 0:
        return subprocess.run(['/usr/bin/sudo', *([] if sys.stdin.isatty() and sys.stdout.isatty() else ['-n']), HELPER, *arguments], check=False).returncode
    try:
        updates = Updates()
        if args.action == 'plan':
            value = updates.plan()
        elif args.action == 'refresh':
            value = updates.refresh()
        elif args.action == 'apply':
            value = updates.apply(args.plan, args.recovery_point, args.confirm)
        elif args.action == 'status':
            value = {'repositories': repository_status(), 'reboot': reboot_status()}
            if (STATE / 'status.json').exists():
                safe_file(STATE / 'status.json')
                value['last_transaction'] = json.loads((STATE / 'status.json').read_text())
        else:
            value = {'automatic_rollback': False, 'steps': [
                'Stop retrying if an update failed. Keep the console or SSH recovery session open.',
                'Review sudo cat /var/lib/rougarou/updates/transactions/TRANSACTION/result.json and install.log.',
                'Run sudo dpkg --audit. Repair incomplete configuration only after reviewing the transaction and recovery point.',
                'If restoring a VM snapshot, use the hypervisor console, verify VM identity/disks and the exact snapshot, and deliberately confirm restore there.',
                'A VM restore discards changes since the snapshot. Protect external disks, databases and remote side effects separately.',
                'Package downgrades do not reverse application data migrations. Rougarou never automatically reverts or reboots.']}
        if args.json or args.action in {'status', 'refresh', 'apply'}:
            print(json.dumps(value, indent=2, sort_keys=True))
        elif args.action == 'recovery':
            print('\n'.join(f'{n}. {line}' for n, line in enumerate(value['steps'], 1)))
        else:
            print(f"Plan {value['plan_id']}\n{len(value['packages'])} package changes; {value['download_bytes']} download bytes; no removals or downgrades.")
            for package in value['packages']:
                print(f"{package['name']}: {package['old_version'] or '(new)'} -> {package['version']} [{package['channel']}]\n  SHA256 {package['sha256']}")
            print('Reboot: ' + ('already required' if value['reboot_required_now'] else 'may be needed after these updates' if value['reboot_may_be_needed'] else 'no package hint; verify after applying'))
            print('Review service impact and create a VM recovery point before applying. No reboot is automatic.')
        return 0
    except KeyboardInterrupt:
        print('Update interrupted. Inspect rougarou update recovery before retrying.', file=sys.stderr)
        return 130
    except (UpdateError, OSError, ValueError, subprocess.TimeoutExpired) as error:
        print('Rougarou update: ' + (str(error) if isinstance(error, UpdateError) else 'Local update data or command is unavailable; inspect recovery.'), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
