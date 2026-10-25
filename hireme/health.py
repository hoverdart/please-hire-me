"""Small operational reads. No recovery, inference, mailbox reads or application writes."""
from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import sqlite3
import subprocess
import time
from types import SimpleNamespace

from .config import validate_settings
from .storage import capacity
from .util import atomic_json, now


def _age(value, clock):
    try:
        parsed=datetime.fromisoformat(value)
        if parsed.tzinfo is None:return None
        age=clock-parsed.timestamp()
        return age if age>=-300 else None
    except (ValueError,TypeError,OverflowError):return None


def check_health(root, *, services=True, verify_login=True, clock=None):
    root=Path(root).expanduser().absolute()
    clock=time.time() if clock is None else clock
    result={'checked_at':now(),'ready':False,'failures':[],'warnings':[],
            'evidence_scope':'operational_health_only','submissions_or_messages_sent':0}
    failures=result['failures'];warnings=result['warnings']
    db=None
    try:
        if root.is_symlink() or (root/'ledger.sqlite3').is_symlink():raise ValueError()
        result['storage']=capacity(root)
        if result['storage']['critical']:failures.append('storage_critical')
        elif result['storage']['low']:warnings.append('storage_low')
        # SQLite may coordinate its WAL shared-memory file. Query-only mode
        # prevents changing applicant rows; this avoids copying a multi-GB DB
        # every 15 minutes merely to read a few indexed status records.
        db=sqlite3.connect((root/'ledger.sqlite3').as_uri()+'?mode=ro',uri=True,timeout=3)
        db.row_factory=sqlite3.Row
        db.execute('PRAGMA query_only=ON');db.execute('BEGIN')
        settings=validate_settings(json.loads(db.execute('SELECT value FROM config WHERE id=1').fetchone()[0]))
        result['paused']=not settings['live_enabled']
        run=db.execute('SELECT started,finished,status,submitted FROM runs ORDER BY started DESC LIMIT 1').fetchone()
        result['latest_run']=dict(run) if run else None
        if run:
            age=_age(run['finished'] or run['started'],clock)
            if age is None:failures.append('run_timestamp_invalid')
            elif run['status']=='running' and age>settings['cycle_timeout_seconds']+300:failures.append('worker_stalled')
            elif run['status']!='running' and age>settings['schedule_hours']*3600+1800:warnings.append('scheduled_cycle_overdue')
            if run['status'] in {'blocked','interrupted'}:warnings.append('latest_cycle_needs_review')
        else:warnings.append('no_recorded_cycle')
        result['outcomes']=dict(db.execute('SELECT state,COUNT(*) FROM application_records GROUP BY state'))
        latest_source=db.execute("SELECT MAX(checked) FROM sources WHERE status='ok'").fetchone()[0]
        result['discovery']={'last_available_check':latest_source,
                             'states':dict(db.execute('SELECT status,COUNT(*) FROM sources GROUP BY status'))}
        source_age=_age(latest_source,clock)
        if settings['live_enabled'] and (source_age is None or source_age>86400):warnings.append('discovery_checks_overdue')
        result['reports']=dict(db.execute('SELECT state,COUNT(*) FROM report_outbox GROUP BY state'))
        pending=db.execute("SELECT MIN(created) FROM report_outbox WHERE state='pending'").fetchone()[0]
        if pending and (_age(pending,clock) is None or _age(pending,clock)>7200):warnings.append('report_delivery_overdue')
        if result['reports'].get('uncertain',0):warnings.append('report_delivery_uncertain')
        from .release_check import _integrations, _scheduler
        facts=lambda:{row['key']:dict(row) for row in db.execute('SELECT * FROM facts WHERE confirmed=1')}
        view=SimpleNamespace(root=root,db=db,settings=lambda:settings,facts=facts)
        result['integrations']=_integrations(view,settings)
        if not result['integrations']['ready']:failures.append('integration_configuration_incomplete')
        if verify_login and settings['provider'].endswith('-cli'):
            from .connections import status
            result['model_login_verified']=status(view,True)['ready']
            if not result['model_login_verified']:failures.append('model_login_unavailable')
        if services:
            result['scheduler']=_scheduler(view)
            schedule=result['scheduler']
            if not (schedule.get('ready') and schedule.get('installed_count')==1
                    and schedule.get('enabled') and schedule.get('active')):failures.append('scheduler_unavailable')
            service=subprocess.run(['systemctl','--user','is-active','please-hire-me-dashboard.service'],
                                   capture_output=True,text=True,timeout=5)
            result['dashboard_active']=service.returncode==0
            if not result['dashboard_active']:failures.append('dashboard_unavailable')
        ack=root/'operations'/'backup-ack.json'
        backup=json.loads(ack.read_text()) if ack.is_file() and not ack.is_symlink() else {}
        if not isinstance(backup,dict):raise ValueError('Invalid backup status')
        age=_age(backup.get('verified_at'),clock)
        result['backup']={'verified_at':backup.get('verified_at'),'age_seconds':age}
        if age is None or age>7*3600:warnings.append('off_device_backup_overdue')
    except (OSError,ValueError,TypeError,KeyError,sqlite3.Error,subprocess.SubprocessError):
        failures.append('health_check_unavailable')
    finally:
        if db is not None:db.close()
    result['ready']=not failures and not warnings
    result['status']='critical' if failures else 'warning' if warnings else 'ok'
    return result


def record_health(root, result):
    atomic_json(Path(root)/'operations'/'health.json',result)
