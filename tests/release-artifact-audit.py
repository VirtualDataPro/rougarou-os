#!/usr/bin/env python3
"""Offline artifact audit; run after extracting base/Codex debs in a container.

Expected read-only mounts: /src (checkout), /out (release), /cache (downloads).
Extracted packages: /tmp/base, /tmp/codex and /tmp/herdr; Codex help: /tmp/codex-help.
"""
import hashlib
import json
from pathlib import Path
import runpy
import stat
import sys
import tarfile

def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()

versions = dict(line.split('=', 1) for line in Path('/src/versions.env').read_text().splitlines()
                if line and not line.startswith('#'))
base = Path('/tmp/base')
source = Path('/src/rootfs')
compared = 0
for path in source.rglob('*'):
    if '__pycache__' in path.parts or path.suffix in ('.pyc', '.pyo'):
        continue
    target = base / path.relative_to(source)
    if path.is_symlink():
        assert target.is_symlink() and path.readlink() == target.readlink(), str(path)
    elif path.is_file():
        assert target.is_file() and sha(path) == sha(target), f'Packaged source differs: {path}'
        assert stat.S_IMODE(path.stat().st_mode) == stat.S_IMODE(target.stat().st_mode), f'Packaged mode differs: {path}'
        compared += 1

codex = Path('/tmp/codex/opt/codex')
archive = Path('/cache/codex') / (versions['CODEX_VERSION'] + '-' + versions['CODEX_ASSET'])
assert sha(archive) == versions['CODEX_SHA256'], 'Codex source archive checksum mismatch'
official = set()
with tarfile.open(archive, 'r:gz') as package:
    for item in package:
        name = item.name.rstrip('/')
        target = codex / name
        if item.isfile():
            official.add(name)
            stream = package.extractfile(item)
            assert sha(target) == hashlib.file_digest(stream, 'sha256').hexdigest(), name
        elif item.issym():
            official.add(name)
            assert target.is_symlink() and str(target.readlink()) == item.linkname, name
assert {str(path.relative_to(codex)) for path in codex.rglob('*') if path.is_file() or path.is_symlink()} == official

help_text = Path('/tmp/codex-help').read_text()
for flag in ('--approve-for-me', '--no-alt-screen', '--oss', '--local-provider'):
    assert flag in help_text, f'Bundled Codex does not support {flag}'
sys.path.insert(0, '/tmp/base/usr/lib/rougarou')
cli = runpy.run_path('/tmp/base/usr/lib/rougarou/cli.py')
assert cli['codex_arguments'](help_text) == ['codex', '--approve-for-me', '--no-alt-screen']

herdr = Path('/tmp/herdr')
assert sha(herdr / 'usr/bin/herdr') == versions['HERDR_SHA256']
herdr_source = Path('/cache/herdr') / (versions['HERDR_VERSION'] + '-source.tar.gz')
assert sha(herdr_source) == versions['HERDR_SOURCE_SHA256']
with tarfile.open(herdr_source) as source:
    expected = source.extractfile(f"herdr-{versions['HERDR_VERSION']}/LICENSE").read()
    assert (herdr / 'usr/share/doc/rougarou-herdr/copyright').read_bytes() == expected
dependencies = json.loads((herdr / 'usr/share/doc/rougarou-herdr/dependencies.json').read_text())
for dependency in dependencies:
    source_archive = Path('/cache/herdr/crates') / f"{dependency['name']}-{dependency['version']}.crate"
    assert sha(source_archive) == dependency['sha256']
    with tarfile.open(source_archive) as archive:
        prefix = f"{dependency['name']}-{dependency['version']}"
        for name in dependency['notices']:
            actual = herdr / 'usr/share/doc/rougarou-herdr/dependency-notices' / prefix / name
            assert actual.read_bytes() == archive.extractfile(prefix + '/' + name).read(), str(actual)
    for item in dependency.get('supplemental_notices', []):
        actual = herdr / 'usr/share/doc/rougarou-herdr/dependency-notices' / f"{dependency['name']}-{dependency['version']}" / item['name']
        assert sha(actual) == item['sha256'], str(actual)

gemini_check = runpy.run_path('/src/tests/gemini-artifact-check.py')['audit']
gemini_evidence = gemini_check(Path('/out/bootstrap-repository'), Path('/out/custom-packages/sources/gemini'), versions)

for root in (base, Path('/tmp/codex'), herdr):
    for path in root.rglob('*'):
        name = str(path.relative_to(root))
        if path.is_file():
            assert '__pycache__' not in name
            assert not name.startswith(('root/', 'home/', 'etc/ssh/ssh_host_', 'var/lib/rougarou/'))
            assert path.name not in ('auth.json', 'credentials.json', 'id_rsa', 'id_ed25519', 'id_ecdsa')
            if path.name == 'machine-id':
                assert path.stat().st_size == 0, f'Nonempty machine identity: {name}'

artifact = f"rougarou-os-{versions['ROUGAROU_VERSION']}-{versions['DEBIAN_ARCH']}.iso"
manifest = json.loads((Path('/out') / f'{artifact}.packages.json').read_text())
installed = {entry['package'] for entry in manifest['packages']}
for entry in manifest['packages']:
    file = Path('/out/bootstrap-repository') / entry['file']
    assert sha(file) == entry['sha256'], entry['file']
assert {'rougarou-base', 'rougarou-codex', 'rougarou-herdr', 'rougarou-gemini', 'nodejs', 'python3', 'git', 'gh', 'openssh-server', 'qemu-guest-agent',
        'ncdu', 'eza', 'fzf', 'starship', 'bat', 'bind9-utils', 'bind9-dnsutils', 'mtr-tiny',
        'docker.io', 'docker-cli', 'rootlesskit', 'podman'}.issubset(installed)
assert not any(package.startswith(('hyprland', 'xserver-xorg', 'gnome-shell', 'plasma-desktop', 'sddm', 'gdm3', 'lightdm')) for package in installed)
print(json.dumps({'result': 'PASS', 'rootfs_files_match': compared, 'codex_official_files_match': len(official),
                  'package_hashes_match': len(installed), 'codex_flags_supported': True,
                  'herdr_official_binary_matches': True, 'herdr_dependency_sources_and_notices_match': len(dependencies),
                  **gemini_evidence,
                  'default_desktop_packages': False, 'credentials_or_machine_identity_in_custom_payload': False}, indent=2))
