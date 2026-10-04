"""Detect interrupted records using the actual shared worker lock; recover paused."""
from __future__ import annotations

from .store import worker_lock
from .util import Blocked


def _unfinished(store):
    return {
        'interrupted_runs': store.db.execute("SELECT COUNT(*) FROM runs WHERE status='running'").fetchone()[0],
        'unfinished_submissions': store.db.execute("SELECT COUNT(*) FROM applications WHERE state='submitting'").fetchone()[0],
        'unfinished_accounts': store.db.execute("SELECT COUNT(*) FROM employer_accounts WHERE state IN ('creating','signing_in')").fetchone()[0],
    }


def worker_status(store):
    try:
        with worker_lock(store.root):
            pending = _unfinished(store)
            return {'running': False, 'recovery_needed': any(pending.values()), **pending}
    except Blocked as error:
        if error.reason != 'worker_busy': raise
        return {'running': True, 'recovery_needed': False,
                'interrupted_runs': 0, 'unfinished_submissions': 0, 'unfinished_accounts': 0}


def recover_interrupted(store):
    # Never reinterpret an in-flight worker's intents. This same lock covers external
    # CLI runs and scheduled runs, not only the dashboard's own background thread.
    with worker_lock(store.root):
        pending = _unfinished(store)
        store.update_settings({'live_enabled': False})
        store.recover()
        store.event('manual_worker_recovery', 'worker', pending)
    return {'paused': True, **pending,
            'message': 'Interrupted work recovered. Applications remain paused. Verify uncertain outcomes in Needs you before continuing.'}
