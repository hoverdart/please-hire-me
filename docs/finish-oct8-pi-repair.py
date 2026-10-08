"""Finish the already-authorized Pi repair after restoring Pi Connect access.

Run from ~/please-hire-me with .venv/bin/python after pulling origin/main.
This deliberately refuses to restart an unrelated listener or untested code.
"""
import json
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import sys
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

repo = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo))
from hireme import scheduler
from hireme.store import Store, worker_lock

tested = 'b6eaaf10bfc396d93b44bab10fcbe129fb7bbaa5'
root = Path.home() / '.local/share/please-hire-me'
backup = root.parent / 'please-hire-me-repair-20261008'
assert sys.platform == 'linux' and Path.cwd().resolve() == repo
assert (backup / 'ledger.sqlite3').is_file()
assert (backup / 'crontab.txt').is_file()
subprocess.run(['git', 'diff', '--quiet', tested, 'HEAD', '--', '.', ':!docs'], cwd=repo, check=True)
assert not subprocess.check_output(['git', 'status', '--porcelain'], cwd=repo).strip()

with worker_lock(root):
    subprocess.run([sys.executable, '-m', 'pytest', '-q',
        'tests/test_oct8_answer_resolution.py', 'tests/test_answer_regressions.py',
        'tests/test_answer_reuse.py', 'tests/test_saved_answers.py',
        'tests/test_basic_context.py', 'tests/test_provider.py',
        'tests/test_eligibility_holds.py', 'tests/test_lever_postings.py',
        'tests/test_browser.py::test_model_budget_and_rate_limit_stop_before_employer_write'],
        cwd=repo, check=True)
    store = Store(root)
    original = sqlite3.connect('file:' + str(backup / 'ledger.sqlite3') + '?mode=ro', uri=True)
    original.row_factory = sqlite3.Row
    for table, key in [('applications', 'id'), ('facts', 'key'), ('answers', 'id'),
                       ('templates', 'id'), ('report_outbox', 'id')]:
        current = {r[key]: dict(r) for r in store.db.execute('SELECT * FROM ' + table)}
        assert all(current.get(r[key]) == dict(r) for r in original.execute('SELECT * FROM ' + table)), table
    original.close()
    config = json.loads((backup / 'baseline.json').read_text())['settings']
    store.update_settings({k: config[k] for k in ('live_enabled', 'gmail_reports')})
    owner = subprocess.run(['fuser', '-n', 'tcp', '8766'], capture_output=True, text=True)
    pids = [int(p) for p in owner.stdout.split()]
    assert owner.returncode in (0, 1) and len(pids) <= 1
    for pid in pids:
        args = Path(f'/proc/{pid}/cmdline').read_bytes().decode().strip('\0').split('\0')
        assert args[1:4] == ['-m', 'hireme', 'dashboard'], 'Port 8766 belongs to another service'
        os.kill(pid, signal.SIGTERM)
    for _ in range(20):
        check = subprocess.run(['fuser', '-n', 'tcp', '8766'], capture_output=True)
        if check.returncode == 1:
            break
        time.sleep(0.25)
    else:
        raise RuntimeError('The dashboard listener did not stop')
    log_path = backup / 'dashboard-restored.log'
    descriptor = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(descriptor, 'ab') as log:
        dashboard = subprocess.Popen([sys.executable, '-m', 'hireme', 'dashboard'], cwd=repo,
            stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True)
    token = json.loads((root / 'config/dashboard-token.json').read_text())['token']
    def status(path, authenticated=False):
        headers = {'X-Hireme-Token': token} if authenticated else {}
        try:
            with urlopen(Request('http://127.0.0.1:8766' + path, headers=headers), timeout=15) as response:
                return response.status
        except HTTPError as error:
            return error.code
    for _ in range(20):
        if dashboard.poll() is not None:
            raise RuntimeError('Dashboard exited before health verification')
        try:
            assert status('/') == 200
            break
        except OSError:
            time.sleep(0.25)
    else:
        raise RuntimeError('Dashboard did not become ready')
    assert status('/api/state') == 403
    assert status('/api/state', True) == 200
    subprocess.run(['crontab', str(backup / 'crontab.txt')], check=True)
    assert subprocess.check_output(['crontab', '-l']) == (backup / 'crontab.txt').read_bytes()
    schedule = scheduler.status(store)
    assert schedule.get('installed') and schedule.get('matches_applicant') and schedule.get('interval_hours') == 6
    assert store.settings()['max_model_requests_per_day'] == 300
    assert store.settings()['max_model_requests_per_cycle'] == 100
    assert store.settings()['model_timeout_seconds'] == 180
    print(json.dumps({'source': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=repo).decode().strip(),
        'dashboard_pid': dashboard.pid, 'dashboard_http': [200, 403, 200], 'schedule': schedule,
        'live_enabled': store.settings()['live_enabled'], 'gmail_reports': store.settings()['gmail_reports']}))
