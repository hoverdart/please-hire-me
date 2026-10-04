from __future__ import annotations

import json
import time
import uuid
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .browser import Browser
from .discovery import sweep_boards,sweep_lists,sweep_portals
from .policy import eligible
from .util import Blocked,now
from .store import worker_lock
from .job_holds import ready, hold, clear, safe_state as safe_job_state


def cycle(store,repo,discover=True,live=True,limit=None,browser_factory=Browser,max_attempts=None,job_ids=None,requested_generation=None):
    with worker_lock(store.root):
        store.recover()
        store.run_generation=store.control_generation() if live else None
        store.preparation_generation=(store.control_generation() if requested_generation is None else requested_generation) if not live else None
        rid=uuid.uuid4().hex; s=store.settings(); count=0; attempts=0; reasons={}; outcomes={}; start=time.monotonic();discovered_count=None;eligible_count=None
        store.run_deadline=start+s['cycle_timeout_seconds']
        store.active_run_id=rid
        max_attempts=min(max_attempts or s['max_attempts_per_cycle'],s['max_attempts_per_cycle'])
        store.db.execute("INSERT INTO runs(id,started,status,detail) VALUES(?,?,'running',?)",(rid,now(),json.dumps({'mode':'live' if live else 'prepare'})))
        try:
            if store.missing_setup() or not s['onboarding_complete']:
                raise Blocked('setup_incomplete',', '.join(store.missing_setup()))
            store.checkpoint()
            if live and not s['live_enabled']:raise Blocked('paused')
            jobs_before=store.db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0]
            if discover:
                store.checkpoint(); sweep_lists(store)
                store.checkpoint(); sweep_portals(store)
                store.checkpoint(); sweep_boards(store,repo)
            discovered_count=store.db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0]-jobs_before if discover else 0
            query="SELECT * FROM jobs WHERE status IN ('discovered','blocked','prepared') AND id NOT IN (SELECT job_id FROM job_decisions)"
            selected=tuple(job_ids or ())
            if selected:query+=' AND id IN ('+','.join('?' for _ in selected)+')'
            rows=list(store.db.execute(query+' ORDER BY score DESC,first_seen DESC',selected))
            ranked=[]
            for row in rows:
                store.checkpoint()
                job=json.loads(row['payload'])
                if not ready(store,job):continue
                try:
                    score,evidence=eligible(job,s,store.facts())
                    store.db.execute("UPDATE jobs SET score=?,reason='' WHERE id=?",(score,job['id']))
                    ranked.append((score,job))
                except Blocked as e:
                    store.block(job['id'],e.reason,e.detail);hold(store,job,e.reason,e.detail,'screening');reasons[e.reason]=reasons.get(e.reason,0)+1
                    store.event('job_screening_blocked',job['id'],{'run_id':rid,'outcome':'blocked','reason':e.reason,'detail':e.detail})
            eligible_count=len(ranked)
            today=datetime.now(ZoneInfo(s['timezone'])).date()
            sent_today=sum(1 for r in store.db.execute("SELECT attempted FROM applications WHERE state='confirmed'")
                          if r[0] and datetime.fromisoformat(r[0]).astimezone(ZoneInfo(s['timezone'])).date()==today)
            # Seven normally; use spare slots when behind the daily trajectory. Never exceed ten.
            expected=max(s['target_per_cycle'],(s['target_per_day']*datetime.now(ZoneInfo(s['timezone'])).hour)//24-sent_today)
            target=min(s['max_per_cycle'],max(s['target_per_cycle'],expected),s['max_per_day']-sent_today)
            if limit is not None:target=min(target,limit)
            if ranked and target>0:
                with worker_lock(store.root,'browser'):
                    with browser_factory(store) as browser:
                        for _,job in sorted(ranked,key=lambda x:x[0],reverse=True):
                            if count>=target or (max_attempts is not None and attempts>=max_attempts):break
                            store.checkpoint()
                            try:
                                store.check_job_decision(job['id'])
                                attempts+=1
                                store.event('application_started',job['id'],{'run_id':rid,'attempt':attempts,'company':job['company'],'title':job['title']})
                                outcome=browser.apply(job,live=live)
                                clear(store,job['id'])
                                outcomes[outcome]=outcomes.get(outcome,0)+1
                                store.event('application_finished',job['id'],{'run_id':rid,'outcome':outcome})
                                store.db.execute('UPDATE jobs SET status=? WHERE id=? AND id NOT IN (SELECT job_id FROM job_decisions)',(outcome,job['id']))
                                if outcome=='confirmed' or not live and outcome=='prepared':count+=1
                            except Blocked as e:
                                store.event('application_finished',job['id'],{'run_id':rid,'outcome':'blocked','reason':e.reason,'detail':e.detail})
                                if e.reason=='model_budget_exhausted' and safe_job_state(store,job['id']):
                                    store.block(job['id'],e.reason,e.detail);hold(store,job,e.reason,e.detail)
                                if e.reason in ('paused','cycle_timeout','model_budget_exhausted','provider_rate_limited'):raise
                                reasons[e.reason]=reasons.get(e.reason,0)+1
                                # Unknown outcomes must retain their distinct state.
                                state=store.db.execute('SELECT state FROM applications WHERE job_id=?',(job['id'],)).fetchone()
                                if not state or state[0] not in ('unknown','submitting','awaiting_verification'):
                                    store.block(job['id'],e.reason,e.detail);hold(store,job,e.reason,e.detail)
                            except Exception as e:
                                store.checkpoint()
                                store.event('application_finished',job['id'],{'run_id':rid,'outcome':'error','error':type(e).__name__,'detail':str(e)[:1200]})
                                reasons['browser_error']=reasons.get('browser_error',0)+1
                                if safe_job_state(store,job['id']):
                                    store.block(job['id'],'browser_error',type(e).__name__);hold(store,job,'browser_error',type(e).__name__)
            store.checkpoint()
            detail=json.dumps({'target':target,'attempts':attempts,'outcomes':outcomes,'confirmed':count if live else 0,'prepared':count if not live else 0,'shortfall':max(0,target-count),'reasons':reasons,'mode':'live' if live else 'prepare','discovered':discovered_count,'eligible':eligible_count})
            store.db.execute("UPDATE runs SET finished=?,status='finished',submitted=?,detail=? WHERE id=?",(now(),count if live else 0,detail,rid))
            return json.loads(detail)
        except Exception as e:
            store.recover()
            status='paused' if isinstance(e,Blocked) and e.reason=='paused' else 'blocked'
            store.db.execute("UPDATE runs SET finished=?,status=?,submitted=?,detail=? WHERE id=?",(now(),status,count if live else 0,json.dumps({'confirmed':count if live else 0,'prepared':count if not live else 0,'attempts':attempts,'reason':str(e),'mode':'live' if live else 'prepare','discovered':discovered_count,'eligible':eligible_count}),rid))
            raise
        finally:
            store.run_generation=None
            store.preparation_generation=None
            store.run_deadline=None
            store.active_run_id=None
            try:
                from .reports import queue_report,flush_reports
                queue_report(store,rid)
                if live:flush_reports(store)
            except Exception as e:
                store.event('batch_report_failed',rid,{'reason':getattr(e,'reason',type(e).__name__)})


def daemon(store,repo):
    # Only one scheduling loop per shared identity ledger, including across clones.
    with worker_lock(store.root,'scheduler'):
        while True:
            try:cycle(store,repo)
            except Blocked as e:store.event('cycle_blocked','scheduler',{'reason':e.reason,'detail':e.detail})
            except Exception as e:store.event('cycle_failed','scheduler',{'type':type(e).__name__})
            seconds=store.settings()['schedule_hours']*3600
            for _ in range(seconds):time.sleep(1)
