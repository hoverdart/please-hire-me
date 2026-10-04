from __future__ import annotations

import json
import secrets
import re
import threading
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs,urlsplit,unquote

from .config import FACTS,REQUIRED
from .store import Store
from .util import private_dir,atomic_json

MAX_BODY=21*1024*1024


def _action_failure(error, discovery_only=False):
    from .util import Blocked
    if isinstance(error, Blocked) and error.reason == 'paused': return None
    if isinstance(error, Blocked) and error.reason == 'cycle_timeout':
        return 'Your last batch reached its time budget. Completed progress is saved. You can adjust the time budget in Preferences before starting another batch.'
    reason = error.reason.replace('_', ' ') if isinstance(error, Blocked) else {
        'PermissionError': 'access to a required local file was denied',
        'FileNotFoundError': 'a required local file was not found',
        'OSError': 'a local file could not be opened or closed',
        'RuntimeError': 'the worker could not start normally',
        'ValueError': 'its settings or local records could not be read',
    }.get(type(error).__name__, 'the worker encountered ' + type(error).__name__)
    action = 'opportunity search' if discovery_only else 'application batch'
    return f'Your last {action} stopped because {reason}. Review the recorded batch and setup checks before trying again.'


def dashboard_url(root,port=8766):
    path=private_dir(root/'config')/'dashboard-token.json'
    if path.is_symlink():raise ValueError('Unsafe dashboard token')
    if path.exists():
        token=json.loads(path.read_text()).get('token','')
        if not re.fullmatch(r'[A-Za-z0-9_-]{40,100}',token):raise ValueError('Invalid dashboard token file')
    else:
        token=secrets.token_urlsafe(32);atomic_json(path,{'token':token})
    return f'http://127.0.0.1:{port}/#token={token}'


def serve(root,repo,port=8766,token=None,demo=False,open_browser=False):
    token=token or (secrets.token_urlsafe(32) if demo else dashboard_url(root,port).split('#token=',1)[1]); state={'running':False,'mode':None,'error':None,'lock':threading.Lock()}
    assets=Path(__file__).parent/'static'
    def start_cycle(discovery_only=False,preparation_only=False):
        generation=None
        if discovery_only or preparation_only:
            control=Store(root)
            try:
                with control.transaction():
                    if preparation_only:
                        if control.settings()['live_enabled']:raise ValueError('Pause automatic submissions before preparing drafts.')
                        if not control.settings()['onboarding_complete'] or control.missing_setup():raise ValueError('Finish Setup checklist before preparing drafts.')
                    generation=control.control_generation()
            finally:control.close()
        with state['lock']:
            if state['running']:raise ValueError('A dashboard-triggered run is already active')
            state['running']=True
            state['mode']='discovery' if discovery_only else 'prepare' if preparation_only else 'live'
            state['error']=None
        def run():
            worker=None;failure=None
            try:
                worker=Store(root)
                if discovery_only:
                    from .opportunity_search import find_opportunities
                    find_opportunities(worker,repo,requested_generation=generation)
                else:
                    from .worker import cycle
                    if preparation_only:cycle(worker,repo,live=False,requested_generation=generation)
                    else:cycle(worker,repo)
            except Exception as e:
                failure=_action_failure(e,discovery_only)
                if worker is not None and failure is not None:
                    try:worker.event('dashboard_run_failed','worker',{'type':type(e).__name__,'message':str(e)[:500]})
                    except Exception:pass
            finally:
                try:
                    if worker is not None:worker.close()
                except Exception as e:
                    failure=failure or _action_failure(e,discovery_only)
                finally:
                    with state['lock']:state['running']=False;state['mode']=None;state['error']=failure
        try:threading.Thread(target=run,name='hireme-dashboard-worker',daemon=True).start()
        except Exception as e:
            with state['lock']:state['running']=False;state['mode']=None;state['error']=_action_failure(e,discovery_only)
            raise

    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass  # URL capability and personal input must not enter access logs.
        def _valid_host(self):
            return self.headers.get('Host') in (f'127.0.0.1:{port}',f'localhost:{port}')
        def _auth(self):
            origin=self.headers.get('Origin')
            return self._valid_host() and secrets.compare_digest(self.headers.get('X-Hireme-Token',''),token) and origin in (None,f'http://127.0.0.1:{port}',f'http://localhost:{port}')
        def _respond_headers(self,code,size,ctype,download=None):
            self.send_response(code)
            self.send_header('Content-Type',ctype)
            self.send_header('Content-Length',str(size))
            self.send_header('Cache-Control','no-store')
            self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('Referrer-Policy','no-referrer')
            if download:self.send_header('Content-Disposition',f'attachment; filename="{download}"')
            self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' blob:; connect-src 'self'; frame-ancestors 'none'; form-action 'self'; base-uri 'none'")
            self.end_headers()

        def send(self,code,data,ctype='application/json',download=None):
            body=json.dumps(data).encode() if ctype=='application/json' else data
            self._respond_headers(code,len(body),ctype,download)
            self.wfile.write(body)

        def send_file(self,path,ctype,download):
            import shutil
            with path.open('rb') as source:
                self._respond_headers(200,path.stat().st_size,ctype,download)
                shutil.copyfileobj(source,self.wfile,length=64*1024)

        def do_GET(self):
            path=urlsplit(self.path).path
            if not self._valid_host():return self.send(403,{'error':'Invalid host'})
            if path in ('/','/app.js','/notes.js','/style.css'):
                name={'/':'index.html','/app.js':'app.js','/notes.js':'notes.js','/style.css':'style.css'}[path]
                return self.send(200,(assets/name).read_bytes(),{'/':'text/html; charset=utf-8','/app.js':'text/javascript','/notes.js':'text/javascript','/style.css':'text/css'}[path])
            if not self._auth():return self.send(403,{'error':'Open the dashboard URL printed by hireme dashboard'})
            store=Store(root)
            try:
                if path=='/api/state':
                    query=parse_qs(urlsplit(self.path).query)
                    try:
                        offset=int(query.get('material_offset',['0'])[0])
                        snapshot=store.snapshot(offset,include_packages=False,question_limit=50,material_search=query.get('material_search',[''])[0],material_status=query.get('material_status',['all'])[0])
                    except ValueError as error:return self.send(400,{'error':str(error)})
                    from .gmail import status as gmail_status
                    from .reports import report_status
                    snapshot['gmail']=gmail_status(store);snapshot['reports']=report_status(store)
                    from .setup_status import readiness
                    if demo:
                        from .demo import DEMO_READINESS
                        snapshot['readiness']={**DEMO_READINESS,'missing':store.missing_setup()}
                    else:snapshot['readiness']=readiness(store)
                    from .ledger import summary
                    snapshot['summary']=summary(store)
                    from .presentation import job_display
                    snapshot['jobs']=[{**job,**job_display(job)} for job in snapshot['jobs']]
                    from .company_controls import annotate_companies
                    snapshot['jobs']=annotate_companies(store,snapshot['jobs'])
                    from .job_holds import annotate
                    snapshot['jobs']=annotate(store,snapshot['jobs'])
                    from .coverage import cycle_funnel
                    snapshot['cycle_funnel']=cycle_funnel(store)
                    snapshot['demo']=demo
                    from .recovery import worker_status
                    activity=worker_status(store)
                    with state['lock']:local_running=state['running'];local_mode=state['mode'];local_error=state['error']
                    if local_running:activity={**activity,'running':True,'recovery_needed':False}
                    snapshot['worker_recovery']=activity
                    running_mode=local_mode
                    if activity['running'] and not running_mode:
                        row=store.db.execute("SELECT detail FROM runs WHERE status='running' ORDER BY started DESC LIMIT 1").fetchone()
                        try:running_mode=json.loads(row['detail'] or '{}').get('mode') if row else None
                        except (TypeError,ValueError):pass
                    return self.send(200,{**snapshot,'fact_labels':FACTS,'required':sorted(REQUIRED),'worker_running':activity['running'],'worker_mode':running_mode,'worker_error':local_error})
                if path=='/api/coverage':
                    from .coverage import coverage
                    return self.send(200,coverage(store))
                if path=='/api/diagnostics':
                    from .doctor import dashboard_diagnostics
                    return self.send(200,dashboard_diagnostics(store,demo=demo))
                if path.startswith('/api/job-note/'):
                    from .job_notes import get_note
                    try:return self.send(200,get_note(store,unquote(path[len('/api/job-note/'):])))
                    except ValueError as error:return self.send(400,{'error':str(error)})
                if path=='/api/jobs':
                    from .ledger import search_jobs
                    query=parse_qs(urlsplit(self.path).query)
                    try:
                        result=search_jobs(store,search=query.get('search',[''])[0],status=query.get('status',['all'])[0],
                            sort=query.get('sort',['recent'])[0],offset=int(query.get('offset',['0'])[0]),include_packages=False)
                    except ValueError as error:return self.send(400,{'error':str(error)})
                    return self.send(200,result)
                if path=='/api/accounts':
                    from .account_ledger import search_accounts
                    query=parse_qs(urlsplit(self.path).query)
                    try:result=search_accounts(store,search=query.get('search',[''])[0],status=query.get('status',['uncertain'])[0],offset=int(query.get('offset',['0'])[0]))
                    except ValueError as error:return self.send(400,{'error':str(error)})
                    return self.send(200,result)
                if path=='/api/questions':
                    from .question_ledger import search_questions
                    query=parse_qs(urlsplit(self.path).query)
                    try:result=search_questions(store,search=query.get('search',[''])[0],offset=int(query.get('offset',['0'])[0]))
                    except ValueError as error:return self.send(400,{'error':str(error)})
                    return self.send(200,result)
                if path=='/api/outcomes':
                    from .outcome_ledger import search_outcomes
                    query=parse_qs(urlsplit(self.path).query)
                    try:result=search_outcomes(store,search=query.get('search',[''])[0],status=query.get('status',['pending'])[0],offset=int(query.get('offset',['0'])[0]))
                    except ValueError as error:return self.send(400,{'error':str(error)})
                    return self.send(200,result)
                if path=='/api/runs':
                    from .run_ledger import search_runs
                    query=parse_qs(urlsplit(self.path).query)
                    try:result=search_runs(store,search=query.get('search',[''])[0],status=query.get('status',['all'])[0],offset=int(query.get('offset',['0'])[0]))
                    except ValueError as error:return self.send(400,{'error':str(error)})
                    return self.send(200,result)
                if path=='/api/sources':
                    from .source_ledger import search_sources
                    query=parse_qs(urlsplit(self.path).query)
                    try:result=search_sources(store,search=query.get('search',[''])[0],status=query.get('status',['all'])[0],offset=int(query.get('offset',['0'])[0]))
                    except ValueError as error:return self.send(400,{'error':str(error)})
                    return self.send(200,result)
                if path.startswith('/api/selected-document/'):
                    from .document_downloads import selected_pdf
                    parts=path[len('/api/selected-document/'):].split('/')
                    if len(parts)!=2:return self.send(400,{'error':'Choose a selected PDF'})
                    try:data,name=selected_pdf(store,parts[0],parts[1])
                    except ValueError as error:return self.send(400,{'error':str(error)})
                    return self.send(200,data,'application/pdf',download=name)
                if path.startswith('/api/application-document/'):
                    from .document_downloads import recorded_pdf
                    parts=path[len('/api/application-document/'):].split('/')
                    if len(parts)!=3:return self.send(400,{'error':'Choose a recorded PDF'})
                    try:data,name=recorded_pdf(store,unquote(parts[0]),int(parts[1]),parts[2])
                    except ValueError as error:return self.send(400,{'error':str(error)})
                    return self.send(200,data,'application/pdf',download=name)
                if path.startswith('/api/application/'):
                    try:record=store.application_record(unquote(path[len('/api/application/'):]))
                    except ValueError as error:return self.send(400,{'error':str(error)})
                    if not record:return self.send(404,{'error':'Application record not found'})
                    return self.send(200,{'application':record})
                if path.startswith('/api/posting-check/'):
                    from .posting_check import check_saved_posting
                    try:result=check_saved_posting(store,unquote(path[len('/api/posting-check/'):]))
                    except ValueError as error:return self.send(400,{'error':str(error)})
                    return self.send(200,result)
                if path=='/api/schedule':
                    if demo:return self.send(200,{'installed':False,'demo':True})
                    from .scheduler import status
                    try:return self.send(200,status(store))
                    except (OSError,ValueError):return self.send(200,{'installed':False,'error':'The system scheduler is unavailable. Use the terminal scheduler or check your operating-system setup.'})
                if path=='/api/saved-answers':
                    from .saved_answers import list_answers
                    query=parse_qs(urlsplit(self.path).query)
                    try:result=list_answers(store,search=query.get('search',[''])[0],offset=int(query.get('offset',['0'])[0]))
                    except ValueError as error:return self.send(400,{'error':str(error)})
                    return self.send(200,result)
                if path=='/api/export.csv':
                    from .ledger import export_csv
                    return self.send(200,export_csv(store),'text/csv; charset=utf-8',download='application-ledger.csv')
                if path.startswith('/api/screenshot/'):
                    name=path.rsplit('/',1)[-1]
                    if '/' in name or '..' in name:return self.send(400,{'error':'Invalid screenshot'})
                    p=root/'screenshots'/name
                    if p.is_symlink() or not p.is_file():return self.send(404,{'error':'Not found'})
                    return self.send(200,p.read_bytes(),'image/jpeg')
                return self.send(404,{'error':'Not found'})
            finally:store.close()
        def do_POST(self):
            if not self._auth():return self.send(403,{'error':'Unauthorized local request'})
            if demo:return self.send(403,{'error':'This is a read-only sample workspace. Start the regular dashboard to save your own information.'})
            try:
                size=int(self.headers.get('Content-Length','0'))
                path=urlsplit(self.path).path
                if path in ('/api/posting-import-preview','/api/posting-import'):
                    from .posting_import import MAX_BYTES
                    if not 0<size<=MAX_BYTES:return self.send(413,{'error':'Choose a CSV up to 1 MiB with at most 500 postings'})
                if path=='/api/backup-check':
                    from .backup_inspection import inspect_upload,MAX_UPLOAD
                    if not 0<size<=MAX_UPLOAD:return self.send(413,{'error':'Choose a history backup ZIP up to 1 GiB'})
                    return self.send(200,inspect_upload(self.rfile,size,root))
                if not 0<size<=MAX_BODY:return self.send(413,{'error':'Request too large'})
                raw=self.rfile.read(size)
                store=Store(root)
                try:
                    if path in ('/api/resume','/api/transcript'):
                        import tempfile
                        from .onboarding import import_resume
                        with tempfile.NamedTemporaryFile(suffix='.pdf',dir=root) as f:
                            f.write(raw);f.flush();result=import_resume(store,Path(f.name),'transcript' if path=='/api/transcript' else 'resume')
                        return self.send(200,{'hash':result['hash'],'candidates':result['candidates'],'repaired':result['repaired']})
                    if path=='/api/gmail-client':
                        import tempfile
                        from .gmail import import_client
                        with tempfile.NamedTemporaryFile(dir=root,suffix='.json') as f:
                            f.write(raw);f.flush();import_client(store,Path(f.name))
                        return self.send(200,{'saved':True})
                    if path=='/api/material-upload':
                        from .materials import import_material
                        result=import_material(store,raw,unquote(self.headers.get('X-Upload-Name','')),self.headers.get('X-Material-Kind',''))
                        return self.send(200,result)
                    if path in ('/api/posting-import-preview','/api/posting-import'):
                        from .posting_import import preview_postings,import_postings
                        result=preview_postings(store,raw) if path.endswith('-preview') else import_postings(store,raw,self.headers.get('X-Import-Hash',''))
                        return self.send(200,result)
                    data=json.loads(raw)
                    if path=='/api/account-credentials':
                        from .accounts import AccountVault
                        from .util import Blocked
                        if not isinstance(data,dict):raise ValueError('Provide employer-scoped credentials')
                        try:
                            result=AccountVault(store).save_supplied(data.get('origin'),data.get('company'),data.get('email'),data.get('password'),data.get('confirmation'))
                        except Blocked:return self.send(409,{'error':'Wait for active work to finish before saving employer credentials'})
                        except OSError:return self.send(400,{'error':'Credentials could not be saved in private local storage'})
                        return self.send(200,result)
                    if path=='/api/job-note':
                        from .job_notes import save_note,NoteConflict
                        try:return self.send(200,save_note(store,data))
                        except NoteConflict as error:return self.send(409,{'error':str(error)})
                    if path=='/api/saved-view':
                        from .saved_views import change_view
                        return self.send(200,change_view(store,data))
                    if path=='/api/account-vault-export':
                        from .account_transfer_web import export_payload
                        if not isinstance(data,dict):raise ValueError('Provide a transfer passphrase and confirmation')
                        payload=export_payload(store,data.get('passphrase'),data.get('confirmation'))
                        return self.send(200,payload,'application/octet-stream',download='account-credentials.encrypted')
                    if path=='/api/account-vault-import':
                        from .account_transfer_web import import_payload
                        if not isinstance(data,dict):raise ValueError('Provide an encrypted transfer and passphrase')
                        result=import_payload(store,data.get('archive'),data.get('passphrase'))
                    elif path=='/api/provider-key':
                        from .connections import save_key
                        save_key(store,data['provider'],data['key']);result={'saved':True}
                    elif path=='/api/provider-check':
                        from .setup_status import readiness
                        result=readiness(store,verify=True)
                    elif path=='/api/transcript-withdraw':
                        from .document_controls import withdraw_transcript
                        from .util import Blocked
                        try:result=withdraw_transcript(store)
                        except Blocked:return self.send(409,{'error':'Wait for the active batch to finish before withdrawing your transcript.'})
                    elif path=='/api/remove-provider-key':
                        from .connections import key_path
                        key_path(store).unlink(missing_ok=True);result={'removed':True}
                    elif path=='/api/basic-context':
                        from .materials import save_basic_context
                        if data.get('confirmed') is not True:raise ValueError('Confirm your factual context before saving')
                        result=save_basic_context(store,data.get('text'),data.get('revision'))
                    elif path=='/api/context-text':
                        from .materials import import_material,review_material
                        text=data['text'];role=data['role']
                        if role not in ('personal','style','reference') or not isinstance(text,str) or not 20<=len(text)<=12000:raise ValueError('Provide 20–12,000 characters and an approved use')
                        material=import_material(store,text.encode(),'Typed context.txt','writing_sample' if role=='style' else 'context')
                        review_material(store,material['id'],text,role,True);result={'saved':True}
                    elif path=='/api/backup':
                        import tempfile
                        from .backup import create_backup
                        from .util import Blocked
                        with state['lock']:
                            if state['running']:raise ValueError('Wait for the active batch to finish before downloading a backup')
                        with tempfile.TemporaryDirectory(prefix='hireme-backup-') as directory:
                            archive=Path(directory)/'application-history.zip'
                            try:create_backup(store,archive)
                            except Blocked as error:
                                if error.reason=='worker_busy':raise ValueError('Wait for the active batch to finish before downloading a backup') from None
                                raise
                            return self.send_file(archive,'application/zip','application-history.zip')
                    elif path=='/api/facts':store.put_facts(data['facts'],clear_keys=data.get('clear'));result={'saved':True}
                    elif path=='/api/answer':store.answer_question(data['id'],data['value'],data.get('fact_key'));result={'saved':True}
                    elif path=='/api/material-review':
                        from .materials import review_material
                        result={'saved':True,**review_material(store,data['id'],data['text'],data['role'],data['confirmed'])}
                    elif path=='/api/reports/flush':
                        from .reports import flush_reports
                        result=flush_reports(store)
                    elif path=='/api/answer-revoke':
                        from .saved_answers import revoke_answer
                        revoke_answer(store,data['id']);result={'revoked':True}
                    elif path=='/api/template':result={'id':store.put_template(data['category'],data['body'])}
                    elif path=='/api/template-edit':result={'id':store.edit_template(data['id'],data['category'],data['body'])}
                    elif path=='/api/template-revoke':store.revoke_template(data['id']);result={'revoked':True}
                    elif path=='/api/recover':
                        with state['lock']:
                            if state['running']:raise ValueError('A batch is still running. Pause it and wait before recovering interrupted work.')
                        from .recovery import recover_interrupted
                        from .util import Blocked
                        try:result=recover_interrupted(store)
                        except Blocked as error:
                            if error.reason=='worker_busy':raise ValueError('A batch is still running. Pause it and wait before recovering interrupted work.') from None
                            raise
                    elif path=='/api/schedule-apply':
                        with state['lock']:
                            if state['running']:raise ValueError('Wait for the current batch to finish before changing its schedule.')
                        from .scheduler import apply_saved_interval
                        from .util import Blocked
                        try:result=apply_saved_interval(store,repo)
                        except Blocked as error:
                            if error.reason=='worker_busy':raise ValueError('Wait for the current batch to finish before changing its schedule.') from None
                            raise
                    elif path=='/api/pause':store.update_settings({'live_enabled':False});result={'paused':True}
                    elif path=='/api/resume-worker':store.update_settings({'live_enabled':True});result={'enabled':True}
                    elif path=='/api/settings':result=store.update_settings(data)
                    elif path=='/api/complete-setup':
                        if store.missing_setup():raise ValueError('Missing: '+', '.join(store.missing_setup()))
                        from .setup_status import readiness
                        checks=readiness(store,verify=True)
                        if not checks['supported_platform']:raise ValueError('Use macOS or Linux; on Windows use WSL2')
                        if not checks['browser_ready'] or not checks['provider']['ready']:raise ValueError('Complete browser and provider setup first')
                        if data.get('start') is not True:
                            store.update_settings({'onboarding_complete':True,'live_enabled':False})
                            result={'message':'Setup saved. Applications remain paused. Resume when ready.'}
                        else:
                            store.update_settings({'onboarding_complete':True,'live_enabled':True})
                            from .scheduler import install
                            try:
                                if store.settings()['deployment']=='pi':
                                    from .pi import install as install_pi
                                    installed=install_pi(store,repo,enable=True)['directory']
                                else:installed=install(store,repo)
                                start_cycle()
                                result={'message':'Automatic applications enabled; schedule installed: '+installed+'. First cycle started.'}
                            except Exception as e:
                                store.update_settings({'live_enabled':False})
                                result={'message':'Setup saved; scheduling failed. Submissions paused. Run hireme daemon or fix the scheduler: '+type(e).__name__}
                    elif path=='/api/reconcile':
                        if type(data.get('submitted')) is not bool:raise ValueError('Choose submitted or not submitted')
                        store.reconcile(data['id'],data['submitted'],data['note']);result={'saved':True}
                    elif path=='/api/account-confirm':
                        from .accounts import AccountVault
                        from .util import Blocked
                        try:AccountVault(store).reconcile(data['id'],data['note'])
                        except Blocked as e:return self.send(409,{'error':e.reason})
                        result={'saved':True}
                    elif path=='/api/company-skip':
                        from .company_controls import skip_company
                        result=skip_company(store,data['id'])
                    elif path=='/api/company-allow':
                        from .company_controls import allow_company
                        result=allow_company(store,data['id'])
                    elif path=='/api/recheck':
                        from .job_holds import recheck
                        from .store import worker_lock
                        from .util import Blocked
                        try:
                            with worker_lock(store.root),store.transaction():result=recheck(store,data['job_id'])
                        except Blocked as error:
                            if error.reason=='worker_busy':return self.send(409,{'error':'Wait for the active batch to finish before rechecking this opportunity.'})
                            raise
                    elif path=='/api/job-decision':
                        store.decide_job(data['id'],data['decision']);result={'saved':True}
                    elif path=='/api/job':
                        from .discovery import posting
                        job=posting(data['url'],data['company'],data['title'],data['location'],'user',data.get('description',''))
                        result={'id':store.upsert_job(job)}
                    elif path=='/api/run':
                        start_cycle();result={'started':True}
                    elif path=='/api/prepare':
                        start_cycle(preparation_only=True);result={'started':True,'mode':'prepare'}
                    elif path=='/api/discover':
                        start_cycle(discovery_only=True);result={'started':True,'mode':'discovery'}
                    else:return self.send(404,{'error':'Not found'})
                    return self.send(200,result)
                finally:store.close()
            except (ValueError,KeyError,TypeError) as e:return self.send(400,{'error':str(e)})
            except Exception as e:return self.send(500,{'error':type(e).__name__})
    server=ThreadingHTTPServer(('127.0.0.1',port),Handler)
    url=f'http://127.0.0.1:{port}/#token={token}'
    print(f'Dashboard: {url}',flush=True)
    if open_browser:
        import webbrowser
        try:
            if not webbrowser.open(url):print('Open the printed URL in your browser.',flush=True)
        except OSError:print('Open the printed URL in your browser.',flush=True)
    try:server.serve_forever()
    finally:server.server_close()
