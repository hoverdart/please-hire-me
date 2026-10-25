"""Capacity checks shared by worker, backups and operational monitoring."""
from __future__ import annotations

from pathlib import Path
import shutil

MIN_FREE_BYTES = 512 * 1024 * 1024


def require_space(path, required=0):
    path=Path(path)
    while not path.exists() and path.parent!=path:path=path.parent
    if shutil.disk_usage(path).free < required + MIN_FREE_BYTES:
        raise ValueError('Insufficient disk space. Free storage or choose another disk; applicant history was preserved.')


def capacity(root):
    root=Path(root)
    usage=shutil.disk_usage(root)
    ledger=root/'ledger.sqlite3'
    return {'free_bytes':usage.free,'total_bytes':usage.total,
            'ledger_bytes':ledger.stat().st_size if ledger.is_file() else 0,
            'critical':usage.free<MIN_FREE_BYTES,
            'low':usage.free<max(2*MIN_FREE_BYTES,usage.total//10)}
