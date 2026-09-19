#!/usr/bin/env python3
"""Record shipped binary hashes, exact versions, source references and build inputs."""
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import runpy
import shutil
import subprocess
import sys
from urllib.parse import quote

payload, iso_url, source = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3])
external_packages = json.loads((payload / 'external-packages.json').read_text())
profile_reader = runpy.run_path(str(source / 'image/package-profiles.py'))
package_profiles = profile_reader['read_profiles'](source / 'image/package-profiles.txt')
core_packages = profile_reader['read_core'](source / 'packages.txt')
licenses = payload / 'licenses' / 'syslinux-common'
licenses.mkdir(parents=True, exist_ok=True)
shutil.copyfile('/usr/share/doc/syslinux-common/copyright', licenses / 'copyright')
shutil.copyfile('/usr/share/common-licenses/GPL-2', licenses / 'GPL-2')
boot_source, boot_version = subprocess.check_output(
    ['dpkg-query', '-W', '-f=${source:Package}\t${source:Version}', 'syslinux-common'], text=True).split('\t')
def sha256(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()

packages = []
for path in sorted((payload / 'repo').glob('*.deb')):
    fields = subprocess.check_output(['dpkg-deb', '-f', str(path), 'Package', 'Version', 'Architecture', 'Source'], text=True)
    data = dict(line.split(': ', 1) for line in fields.splitlines() if ': ' in line)
    package, version = data['Package'], data['Version']
    match = re.fullmatch(r'(\S+)(?: \((.+)\))?', data.get('Source', package))
    source_name, source_version = match[1], match[2] or version
    packages.append({'package': package, 'version': version, 'architecture': data['Architecture'],
                     'file': path.name, 'sha256': sha256(path), 'source_package': source_name,
                     'source_version': source_version,
                     'source_reference': f'https://sources.debian.org/src/{quote(source_name)}/{quote(source_version)}/',
                     'license_reference': f'/usr/share/doc/{package}/copyright'})
    if package.startswith('rougarou-'):
        packages[-1]['source_reference'] = ('https://github.com/openai/codex/tree/rust-v' + os.environ['CODEX_VERSION']
                                            if package == 'rougarou-codex' else 'Rougarou OS project source: rootfs/ and scripts/build-inside-container.sh')
        for external in external_packages.values():
            if package == external['package']:
                packages[-1]['source_reference'] = external['source']
                packages[-1]['source_version'] = external['version']
(payload / 'package-manifest.json').write_text(json.dumps({'packages': packages}, indent=2) + '\n')
inputs = {}
for directory in ('rootfs', 'image', 'scripts', 'containers'):
    for path in sorted((source / directory).rglob('*')):
        if path.is_file() and '__pycache__' not in path.parts and path.suffix not in ('.pyc', '.pyo'):
            inputs[str(path.relative_to(source))] = sha256(path)
for name in ('versions.env', 'packages.txt'):
    inputs[name] = sha256(source / name)
manifest = {
    'name': 'Rougarou OS', 'version': os.environ['ROUGAROU_VERSION'],
    'built_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
    'architecture': os.environ['DEBIAN_ARCH'], 'debian_version': os.environ['DEBIAN_VERSION'],
    'debian_codename': os.environ['DEBIAN_CODENAME'], 'debian_iso': iso_url,
    'debian_verification': 'SHA512SUMS signature verified with Debian role keyring; ISO SHA512 verified',
    'builder_container': os.environ['DEBIAN_CONTAINER'],
    'bios_menu': {'package': 'syslinux-common', 'source_package': boot_source, 'source_version': boot_version,
                  'source_reference': f'https://sources.debian.org/src/{quote(boot_source)}/{quote(boot_version)}/',
                  'license_reference': '/rougarou/licenses/syslinux-common/copyright'},
    'codex': {'version': os.environ['CODEX_VERSION'], 'asset': os.environ['CODEX_ASSET'],
              'sha256': os.environ['CODEX_SHA256'], 'license': 'Apache-2.0',
              'source': f'https://github.com/openai/codex/tree/rust-v{os.environ["CODEX_VERSION"]}'},
    'package_count': len(packages), 'inputs_sha256': inputs,
    'core_package_roots': core_packages + ['rougarou-base'],
    'optional_package_profiles': package_profiles,
    'deferred_agent_profiles': {
        'claude': 'No binary on the ISO; explicit first-login authenticated vendor download',
        'opencode': 'No binary on the ISO; explicit first-login pinned upstream download'},
    'external_packages': external_packages,
    'installer_frontend': json.loads((payload / 'installer-manifest.json').read_text()),
    'reproducibility': 'ISO/container/Codex inputs pinned; apt security updates resolved at build time and recorded in package manifest. Not byte-for-byte reproducible.',
}
(payload / 'build-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
