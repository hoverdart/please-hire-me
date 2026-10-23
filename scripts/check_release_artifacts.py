"""Reject private files and unsafe members without reading applicant contents."""
from __future__ import annotations

import argparse
import re
import stat
import tarfile
import zipfile
from pathlib import Path, PurePosixPath


PRIVATE_DIRECTORIES = {
    'applications', 'screenshots', 'browser', 'platform-browser', 'logs', 'state',
    'integrations', '.git', '.venv', 'venv', '__pycache__',
}
PRIVATE_FILE = re.compile(
    r'(?:\.env(?:\..*)?|ledger\.sqlite3(?:-.*)?|credentials\.json|token\.json|'
    r'profile\.json|settings\.json|answers\.md|dashboard-token\.json|'
    r'gmail-(?:client|token|account)\.json|client_secret[^/]*|[^/]+\.(?:pem|key))',
    re.IGNORECASE,
)


def validate(names):
    seen = set()
    for name in names:
        path = PurePosixPath(name)
        if (not name or '\\' in name or any(ord(char) < 32 for char in name)
                or path.is_absolute() or '..' in path.parts
                or re.match(r'^[A-Za-z]:', name)):
            raise ValueError('Unsafe artifact member path')
        identity = str(path)
        if identity in seen:
            raise ValueError('Duplicate artifact member path')
        seen.add(identity)
        if PRIVATE_FILE.fullmatch(path.name):
            raise ValueError('Private file class in release artifact')
        if any(part.casefold() in PRIVATE_DIRECTORIES for part in path.parts):
            raise ValueError('Private storage directory in release artifact')
    if not seen:
        raise ValueError('Release artifact is empty')


def audit_archive(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError('Choose a regular release archive')
    if path.suffix == '.whl':
        with zipfile.ZipFile(path) as archive:
            entries = archive.infolist()
            validate(entry.filename for entry in entries)
            for entry in entries:
                kind = stat.S_IFMT(entry.external_attr >> 16)
                if kind not in (0, stat.S_IFREG, stat.S_IFDIR):
                    raise ValueError('Distribution archive contains links or special files')
                if entry.flag_bits & 1:
                    raise ValueError('Distribution archive contains encrypted files')
    else:
        with tarfile.open(path) as archive:
            entries = archive.getmembers()
            validate(entry.name for entry in entries)
            if any(not (entry.isfile() or entry.isdir()) for entry in entries):
                raise ValueError('Distribution archive contains links or special files')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archives', nargs='+', type=Path)
    args = parser.parse_args(argv)
    for path in args.archives:
        try:
            audit_archive(path)
        except (OSError, ValueError, tarfile.TarError, zipfile.BadZipFile) as error:
            # Member names can themselves contain personal data.
            detail = str(error) if isinstance(error, ValueError) else 'Unreadable release archive'
            parser.exit(1, f'Artifact audit failed: {detail}\n')
        print('Artifact private-file/path audit passed:', path.name)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
