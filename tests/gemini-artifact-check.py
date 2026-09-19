"""Audit the optional Gemini deb against artifacts retained on the same media."""
import hashlib
import json
from pathlib import Path, PurePosixPath
import subprocess
import tarfile
import tempfile
import zipfile


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def audit(repository, sources, versions):
    version = versions['GEMINI_VERSION']
    candidates = list(repository.glob('rougarou-gemini_*.deb'))
    assert len(candidates) == 1, 'Expected one optional Gemini package'
    archive = sources / versions['GEMINI_ASSET']
    source = sources / f'gemini-cli-{version}-source.tar.gz'
    assert sha(archive) == versions['GEMINI_SHA256'], 'Gemini bundle pin mismatch'
    assert sha(source) == versions['GEMINI_SOURCE_SHA256'], 'Gemini source pin mismatch'
    with tempfile.TemporaryDirectory(prefix='rougarou-gemini-audit-') as temporary:
        root = Path(temporary) / 'package'
        control = Path(temporary) / 'control'
        subprocess.run(['dpkg-deb', '--extract', str(candidates[0]), str(root)], check=True)
        subprocess.run(['dpkg-deb', '--control', str(candidates[0]), str(control)], check=True)
        fields = {}
        for line in (control / 'control').read_text().splitlines():
            if ': ' in line and not line.startswith(' '):
                key, value = line.split(': ', 1)
                fields[key] = value
        assert fields['Package'] == 'rougarou-gemini'
        assert fields['Version'] == f"{version}+rougarou{versions['ROUGAROU_PACKAGE_REVISION']}"
        assert fields['Architecture'] == 'all'
        assert fields['Depends'] == 'nodejs (>= 20), ca-certificates, git, ripgrep'
        assert int(fields['Installed-Size']) >= sum(path.stat().st_size for path in root.rglob('*') if path.is_file()) // 1024
        assert not any((control / name).exists() for name in ('preinst', 'postinst', 'prerm', 'postrm', 'triggers'))
        assert (control / 'conffiles').read_text() == '/etc/gemini-cli/settings.json\n'
        assert json.loads((root / 'etc/gemini-cli/settings.json').read_text()) == {
            'general': {'enableAutoUpdate': False, 'enableAutoUpdateNotification': False}}
        assert (root / 'usr/bin/gemini').read_text() == '#!/bin/sh\nexec /usr/bin/node /usr/lib/rougarou-gemini/gemini.js "$@"\n'
        assert (root / 'usr/bin/gemini').stat().st_mode & 0o777 == 0o755
        runtime = root / 'usr/lib/rougarou-gemini'
        official = set()
        with zipfile.ZipFile(archive) as bundle:
            for item in bundle.infolist():
                relative = PurePosixPath(item.filename)
                assert not relative.is_absolute() and '..' not in relative.parts
                if item.is_dir():
                    continue
                official.add(str(relative))
                actual = runtime / relative
                assert not actual.is_symlink() and sha(actual) == hashlib.sha256(bundle.read(item)).hexdigest(), str(relative)
        assert {str(path.relative_to(runtime)) for path in runtime.rglob('*') if path.is_file()} == official | {'package.json'}
        root_metadata = json.loads((runtime / 'package.json').read_text())
        assert root_metadata['name'] == '@google/gemini-cli' and root_metadata['version'] == version and root_metadata['type'] == 'module'
        documentation = root / 'usr/share/doc/rougarou-gemini'
        with tarfile.open(source) as upstream:
            assert (documentation / 'copyright').read_bytes() == upstream.extractfile(f'gemini-cli-{version}/LICENSE').read()
        assert (documentation / 'THIRD_PARTY_NOTICES').read_bytes() == (runtime / 'bundled/third_party/THIRD_PARTY_NOTICES').read_bytes()
        dependencies = json.loads((documentation / 'dependencies.json').read_text())
        assert dependencies, 'Gemini dependency notices missing'
        for dependency in dependencies:
            identity = hashlib.sha256(dependency['url'].encode()).hexdigest()
            dependency_archive = sources / ('npm-' + identity + '.tgz')
            assert sha(dependency_archive) == dependency['sha256'], dependency['name']
            notices = documentation / 'dependency-notices' / identity
            assert dependency['notices'] or dependency['supplemental_notices']
            with tarfile.open(dependency_archive) as npm:
                for name in dependency['notices']:
                    assert (notices / name).read_bytes() == npm.extractfile(name).read(), dependency['name']
            for item in dependency['supplemental_notices']:
                assert sha(notices / item['name']) == item['sha256'], dependency['name']
                assert sha(sources / ('license-' + item['sha256'] + '.txt')) == item['sha256']
        for path in root.rglob('*'):
            name = str(path.relative_to(root))
            assert not path.is_symlink(), f'Unexpected Gemini payload symlink: {name}'
            assert not name.startswith(('root/', 'home/', 'var/lib/rougarou/'))
            assert path.name not in ('auth.json', 'credentials.json', 'id_rsa', 'id_ed25519', 'machine-id')
    return {'gemini_official_bundle_files_match': len(official),
            'gemini_dependency_sources_and_notices_match': len(dependencies),
            'gemini_offline_package_metadata_matches': True}
