#!/usr/bin/env python3
"""Validate the shared installer profiles and print their Debian package roots."""
import argparse
from pathlib import Path
import re


PACKAGE = re.compile(r'[a-z0-9][a-z0-9+.-]*\Z')
PROFILE = re.compile(r'[a-z][a-z0-9-]*\Z')
CUSTOM_PACKAGES = {'rougarou-base', 'rougarou-codex', 'rougarou-herdr', 'rougarou-gemini'}


def read_core(path):
    roots = []
    for number, line in enumerate(path.read_text().splitlines(), 1):
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        if not PACKAGE.fullmatch(line) or line.startswith('rougarou-'):
            raise ValueError(f'{path}:{number}: expected one Debian core package name')
        if line in roots:
            raise ValueError(f'{path}:{number}: duplicate core package {line}')
        roots.append(line)
    if not roots:
        raise ValueError('Core package list is empty')
    return roots


def read_profiles(path):
    profiles = {}
    for number, line in enumerate(path.read_text().splitlines(), 1):
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        fields = line.split('|')
        if len(fields) != 2 or not PROFILE.fullmatch(fields[0]):
            raise ValueError(f'{path}:{number}: expected profile-id|package roots')
        name, roots = fields[0], fields[1].split()
        if name in profiles:
            raise ValueError(f'{path}:{number}: duplicate profile {name}')
        if not roots or len(set(roots)) != len(roots) or any(not PACKAGE.fullmatch(item) for item in roots):
            raise ValueError(f'{path}:{number}: invalid or duplicate package roots')
        unknown = {item for item in roots if item.startswith('rougarou-')} - CUSTOM_PACKAGES
        if unknown:
            raise ValueError(f'{path}:{number}: unimplemented custom package: {sorted(unknown)}')
        profiles[name] = roots
    return profiles


def debian_roots(core, profiles):
    optional = {package for roots in profiles.values() for package in roots}
    return sorted((set(core) | optional) - CUSTOM_PACKAGES)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=Path('/src'))
    args = parser.parse_args()
    try:
        core = read_core(args.source / 'packages.txt')
        profiles = read_profiles(args.source / 'image/package-profiles.txt')
    except ValueError as error:
        parser.exit(1, str(error) + '\n')
    print('\n'.join(debian_roots(core, profiles)))


if __name__ == '__main__':
    main()
