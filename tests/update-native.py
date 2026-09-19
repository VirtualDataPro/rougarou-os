#!/usr/bin/python3
"""Run only in a disposable Debian container/VM; changes real fixture packages."""
import functools
import hashlib
import http.server
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time

sys.path.insert(0, '/usr/lib/rougarou')
import updates


def run(*argv, **kwargs):
    return subprocess.run(argv, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **kwargs).stdout.decode()


def must_fail(function, code=None):
    try:
        function()
    except updates.UpdateError as error:
        if code:
            assert error.code == code, (error.code, code, str(error))
        return
    raise AssertionError('Unsafe operation was unexpectedly accepted')


def files(root):
    return {str(p.relative_to(root)): (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns)
            for p in root.rglob('*') if p.is_file()}


def build(root, name, version, dependency=None):
    package = root / (name + version)
    (package / 'DEBIAN').mkdir(parents=True)
    (package / 'usr/share' / name).mkdir(parents=True)
    (package / 'usr/share' / name / 'version').write_text(version)
    (package / 'DEBIAN/control').write_text(f'Package: {name}\nVersion: {version}\nArchitecture: all\nMaintainer: Disposable Test <test@example.invalid>\nDescription: isolated guided-update proof\n' + (f'Depends: {dependency}\n' if dependency else ''))
    path = root / f'{name}_{version}_all.deb'
    run('dpkg-deb', '--build', str(package), str(path))
    return path


with tempfile.TemporaryDirectory(prefix='native-guided-update-') as directory:
    root = Path(directory)
    repo = root / 'web/channels/testing'
    repo.mkdir(parents=True)
    secret = root / 'signing'
    secret.mkdir(mode=0o700)
    run('gpg', '--homedir', str(secret), '--batch', '--pinentry-mode', 'loopback', '--passphrase', '', '--quick-generate-key', 'Disposable update test <test@example.invalid>', 'ed25519', 'sign', '1d')
    key = Path(updates.KEY)
    key.parent.mkdir(parents=True, exist_ok=True)
    key.write_bytes(subprocess.check_output(['gpg', '--homedir', str(secret), '--export']))
    key.chmod(0o644)
    one = build(root, 'rougarou-update-fixture', '1')
    two = build(root, 'rougarou-update-fixture', '2', 'rougarou-update-dependency (= 1)')
    dependency = build(root, 'rougarou-update-dependency', '1')
    unrelated = build(root, 'rougarou-unrelated-fixture', '1')
    unrelated_two = build(root, 'rougarou-unrelated-fixture', '2')
    run('dpkg', '-i', str(one), str(unrelated))
    for path in (two, dependency):
        shutil.copy2(path, repo / path.name)
    index = subprocess.check_output(['dpkg-scanpackages', '.', '/dev/null'], cwd=repo)
    (repo / 'Packages').write_bytes(index)
    import email.utils, datetime
    now = datetime.datetime.now(datetime.timezone.utc)
    release = ('Origin: Rougarou\nLabel: Rougarou\nSuite: testing\nCodename: rougarou\n'
               + 'Date: ' + email.utils.format_datetime(now, usegmt=True) + '\n'
               + 'Valid-Until: ' + email.utils.format_datetime(now + datetime.timedelta(days=1), usegmt=True) + '\n'
               + f'SHA256:\n {hashlib.sha256(index).hexdigest()} {len(index)} Packages\n')
    (repo / 'Release').write_text(release)
    run('gpg', '--homedir', str(secret), '--batch', '--yes', '--clearsign', '--output', str(repo / 'InRelease'), str(repo / 'Release'))
    # No production connection: serve only signed disposable fixture bytes on loopback.
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(root / 'web'))
    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        etc = Path('/etc/apt')
        (etc / 'sources.list').unlink(missing_ok=True)
        for path in (etc / 'sources.list.d').iterdir():
            path.unlink()
        source = etc / 'sources.list.d/rougarou.sources'
        source.write_text(f'Types: deb\nURIs: http://127.0.0.1:{server.server_port}/channels/testing\nSuites: ./\nSigned-By: {key}\n')
        if not Path('/etc/machine-id').exists() or not Path('/etc/machine-id').read_text().strip():
            Path('/etc/machine-id').write_text('0123456789abcdef0123456789abcdef\n')
        engine = updates.Updates()
        run('apt-mark', 'auto', 'rougarou-update-fixture')
        automatic_before = updates.auto_packages()
        before = updates.inventory()
        prior_umask = os.umask(0o077)
        try:
            result = engine.refresh()
        finally:
            os.umask(prior_umask)
        assert result['sources'] == 1
        before_plan = files(engine.state)
        plan = engine.plan()
        assert files(engine.state) == before_plan, 'Read-only plan changed persistent metadata'
        unprivileged = json.loads(run('runuser', '-u', 'nobody', '--', '/usr/bin/python3', '-I', '-c',
            "import sys,json;sys.path.insert(0,'/usr/lib/rougarou');import updates;print(json.dumps(updates.Updates().plan()))"))
        assert unprivileged == plan, 'Ordinary account received a different plan'
        assert files(engine.state) == before_plan, 'Ordinary plan changed metadata'
        assert {p['name']: p['version'] for p in plan['packages']} == {'rougarou-update-fixture': '2', 'rougarou-update-dependency': '1'}, plan
        assert not plan['removals'] and not plan['downgrades']
        assert updates.repository_status()['sources'][0]['status'] == 'expiring'
        run('apt-mark', 'hold', 'rougarou-update-fixture')
        assert not engine.plan()['packages'], 'Held package was proposed for upgrade'
        run('apt-mark', 'unhold', 'rougarou-update-fixture')
        assert engine.plan()['plan_id'] == plan['plan_id']
        # A failed refresh may leave diagnostic files but never switches the
        # active authenticated generation to partial/untrusted metadata.
        current = (engine.state / 'current.json').read_bytes()
        release_original = (repo / 'InRelease').read_bytes()
        (repo / 'InRelease').write_bytes(release_original.replace(b'Label: Rougarou', b'Label: Tampered'))
        must_fail(engine.refresh)
        assert (engine.state / 'current.json').read_bytes() == current
        (repo / 'InRelease').write_bytes(release_original)
        (repo / 'Packages').write_bytes(index + b'\nTampered: yes\n')
        must_fail(engine.refresh)
        assert (engine.state / 'current.json').read_bytes() == current
        (repo / 'Packages').write_bytes(index)
        metadata_root, old_receipt = engine.current()
        receipt_path = metadata_root / 'receipt.json'
        receipt_bytes = receipt_path.read_bytes()
        expired_receipt = dict(old_receipt, refreshed_at=time.time() - updates.MAX_AGE - 1)
        receipt_path.write_text(json.dumps(expired_receipt))
        must_fail(engine.plan, 'stale')
        receipt_path.write_bytes(receipt_bytes)
        must_fail(lambda: engine.apply(plan['plan_id'], 'fixture-snapshot', False))
        must_fail(lambda: engine.apply(plan['plan_id'], '', True))
        offpolicy = etc / 'sources.list.d/vendor.sources'
        offpolicy.write_text('Types: deb\nURIs: https://deb.debian.org/debian\nSuites: trixie\nComponents: main\nSigned-By: /usr/share/keyrings/debian-archive-keyring.gpg\n')
        must_fail(engine.plan, 'policy_error')
        offpolicy.unlink()
        run('dpkg', '-i', str(unrelated_two))
        must_fail(lambda: engine.apply(plan['plan_id'], 'fixture-snapshot', True), 'stale')
        assert Path('/usr/share/rougarou-update-fixture/version').read_text() == '1'
        run('dpkg', '-i', str(unrelated))
        assert engine.plan()['plan_id'] == plan['plan_id']
        metadata, receipt = engine.current()
        cached = metadata / receipt['sources'][0]['release']
        original = cached.read_bytes()
        cached.write_bytes(original.replace(b'Label: Rougarou', b'Label: BrokenXX'))
        must_fail(engine.plan)
        cached.write_bytes(original)
        must_fail(lambda: updates.signature(repo / 'InRelease', updates.DEBIAN_KEY), 'invalid_signature')
        # Native APT refuses bad package bytes; restore the fixture and retry
        # only after verifying that no fixture package was installed.
        binary = (repo / two.name).read_bytes()
        (repo / two.name).write_bytes(binary[:-1] + bytes([binary[-1] ^ 1]))
        must_fail(lambda: engine.apply(plan['plan_id'], 'fixture-snapshot', True))
        assert Path('/usr/share/rougarou-update-fixture/version').read_text() == '1'
        assert updates.inventory() == before
        (repo / two.name).write_bytes(binary)
        result = engine.apply(plan['plan_id'], 'disposable-container-fixture (no VM snapshot claim)', True)
        assert result['status'] == 'complete', result
        after = updates.inventory()
        assert after['rougarou-update-fixture']['version'] == '2'
        assert after['rougarou-update-dependency']['version'] == '1'
        assert {k:v for k,v in after.items() if k not in {'rougarou-update-fixture', 'rougarou-update-dependency'}} == {k:v for k,v in before.items() if k not in {'rougarou-update-fixture', 'rougarou-update-dependency'}}
        assert not engine.plan()['packages']
        assert updates.auto_packages() == sorted(set(automatic_before) | {'rougarou-update-dependency'})
        record = json.loads((engine.state / 'transactions' / result['transaction'] / 'before.json').read_text())
        assert record['recovery']['assurance'].startswith('operator-attested')
        assert not run('dpkg', '--audit').strip()
        print(json.dumps({'result':'PASS', 'native_apt_signature': True, 'signed_index_and_deb_hashes': True,
            'real_upgrade':'rougarou-update-fixture 1 -> 2', 'new_dependency':'rougarou-update-dependency 1',
            'unrelated_inventory_preserved': True, 'read_only_plan': True, 'unprivileged_plan':True, 'held_package_preserved':True, 'stale_plan_rejected':True,
            'offpolicy_source_rejected': True, 'tampered_release_rejected':True,
            'wrong_signing_key_rejected':True, 'tampered_refresh_not_activated':True, 'stale_metadata_rejected':True, 'tampered_deb_rejected_before_install':True,
            'automatic_marks_preserved':True, 'transaction_records':True,
            'recovery_point':'operator-attested fixture, no actual VM restore tested'}))
    finally:
        server.shutdown()
