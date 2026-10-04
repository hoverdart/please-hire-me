"""Collect public opportunities without preparing or submitting applications."""
from __future__ import annotations

import json
import time
import uuid

from .discovery import sweep_boards, sweep_lists, sweep_portals
from .net import Network
from .recovery import _unfinished
from .store import worker_lock
from .util import Blocked, now


def find_opportunities(store, repo, deadline_seconds=300, requested_generation=None):
    if type(deadline_seconds) is not int or not 1 <= deadline_seconds <= 1800:
        raise ValueError('Choose a discovery time limit between 1 and 1800 seconds')
    if requested_generation is not None and (type(requested_generation) is not int or requested_generation < 0):
        raise ValueError('Invalid discovery cancellation generation')
    with worker_lock(store.root):
        if any(_unfinished(store).values()):
            raise Blocked('recovery_needed', 'Recover interrupted work before finding opportunities')
        store.discovery_generation = store.control_generation() if requested_generation is None else requested_generation
        results = {}; store.discovery_results = results
        rid = uuid.uuid4().hex
        before = store.db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0]
        deadline = time.monotonic() + deadline_seconds
        store.db.execute("INSERT INTO runs(id,started,status,detail) VALUES(?,?,'running',?)",
                         (rid, now(), json.dumps({'mode': 'discovery'})))
        try:
            net = Network(deadline, checkpoint=store.checkpoint)
            store.checkpoint(); sweep_lists(store, net)
            store.checkpoint()
            if time.monotonic() < deadline:
                sweep_portals(store, net)
            store.checkpoint()
            remaining = deadline - time.monotonic()
            if remaining > 0:
                sweep_boards(store, repo, deadline_seconds=remaining)
            store.checkpoint()
            added = store.db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0] - before
            detail = {'mode': 'discovery', 'added': added, 'submissions': 0,
                      'sources_checked': len(results), 'sources_failed': sum(status == 'error' for status in results.values()),
                      'time_limit_reached': time.monotonic() >= deadline}
            store.db.execute("UPDATE runs SET finished=?,status='finished',submitted=0,detail=? WHERE id=?",
                             (now(), json.dumps(detail), rid))
            return detail
        except Exception as error:
            status = 'paused' if isinstance(error, Blocked) and error.reason == 'paused' else 'blocked'
            added = store.db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0] - before
            detail = {'mode': 'discovery', 'reason': str(error), 'added': added, 'submissions': 0,
                      'sources_checked': len(results), 'sources_failed': sum(status == 'error' for status in results.values())}
            store.db.execute('UPDATE runs SET finished=?,status=?,submitted=0,detail=? WHERE id=?',
                             (now(), status, json.dumps(detail), rid))
            raise
        finally:
            store.discovery_generation = None
            store.discovery_results = None
