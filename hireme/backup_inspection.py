"""Test a private history restore without replacing the active workspace."""
from pathlib import Path
import tempfile

from .backup import restore_backup
from .config import validate_fact
from .store import Store
from .util import now, private_dir

MAX_UPLOAD = 1024 * 1024 * 1024


def inspect_backup(archive, temporary_parent):
    with tempfile.TemporaryDirectory(prefix='.backup-check-', dir=private_dir(temporary_parent)) as directory:
        root = Path(directory) / 'workspace'
        restore_backup(archive, root)
        store = Store(root)
        try:
            owner = {}
            for key, limit in (('full_name', 200), ('email', 320)):
                row = store.db.execute('SELECT value FROM facts WHERE key=? AND confirmed=1', (key,)).fetchone()
                if row and isinstance(row[0], str) and len(row[0]) <= limit:
                    try: owner[key] = validate_fact(key, row[0])
                    except ValueError: pass
            counts = {}
            for key, query in (
                ('opportunities', 'SELECT COUNT(*) FROM jobs'),
                ('applications', 'SELECT COUNT(*) FROM applications'),
                ('confirmations', "SELECT COUNT(*) FROM applications WHERE state='confirmed'"),
                ('outcomes_to_review', "SELECT COUNT(*) FROM applications WHERE state IN ('unknown','submitting','awaiting_verification')"),
                ('unanswered_questions', 'SELECT COUNT(*) FROM questions WHERE resolved=0'),
                ('employer_accounts', 'SELECT COUNT(*) FROM employer_accounts'),
                ('accounts_to_review', "SELECT COUNT(*) FROM employer_accounts WHERE state IN ('creating','signing_in','uncertain')"),
                ('writing_sources', 'SELECT COUNT(*) FROM materials'),
                ('approved_sources', 'SELECT COUNT(*) FROM materials WHERE confirmed=1'),
            ):
                counts[key] = store.db.execute(query).fetchone()[0]
            for key, folder in (('pdf_files', 'documents'), ('source_files', 'materials'), ('evidence_images', 'screenshots')):
                parent = root / folder
                counts[key] = sum(p.is_file() for p in parent.iterdir()) if parent.exists() else 0
            return {'checked_at': now(), 'owner': owner, 'counts': counts,
                    'restores_paused': True, 'credentials_included': False}
        finally:
            store.close()


def inspect_upload(stream, size, temporary_parent):
    if not 0 < size <= MAX_UPLOAD:
        raise ValueError('Choose a history backup ZIP up to 1 GiB')
    try:
        with tempfile.TemporaryDirectory(prefix='.backup-check-upload-', dir=private_dir(temporary_parent)) as directory:
            archive = Path(directory) / 'history.zip'
            with archive.open('xb') as output:
                archive.chmod(0o600)
                remaining = size
                while remaining:
                    chunk = stream.read(min(65536, remaining))
                    if not chunk:
                        raise ValueError('Backup upload was interrupted. Choose the file and try again.')
                    output.write(chunk)
                    remaining -= len(chunk)
            return inspect_backup(archive, Path(directory))
    except OSError:
        raise ValueError('Could not check the backup in private local storage. Check available disk space and try again.') from None
