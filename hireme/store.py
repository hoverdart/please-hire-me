from __future__ import annotations

import contextlib
import fcntl
import json
from itertools import islice
import os
import re
import sqlite3
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .config import DEFAULTS, FACTS, REQUIRED, validate_fact, validate_settings
from .util import Blocked, atomic_json, company_normalizer, digest, now, private_dir

SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_metadata (id INTEGER PRIMARY KEY CHECK(id=1),fingerprint TEXT NOT NULL,schema_version INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS question_contexts (id TEXT PRIMARY KEY,context TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS field_bindings_v2 (id TEXT PRIMARY KEY,context TEXT NOT NULL,
 fact_key TEXT,template_id TEXT,source_revision INTEGER NOT NULL,rule_version INTEGER NOT NULL,created TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS option_mappings (id TEXT PRIMARY KEY,fact_key TEXT NOT NULL,fact_revision INTEGER NOT NULL,
 value TEXT NOT NULL,created TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS model_abstentions (id TEXT PRIMARY KEY,method TEXT NOT NULL,label TEXT NOT NULL,created TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS job_holds (job_id TEXT PRIMARY KEY,category TEXT NOT NULL,reason TEXT NOT NULL,
 dependency TEXT NOT NULL,retry_count INTEGER NOT NULL,retry_at REAL,evidence TEXT NOT NULL,updated TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS job_notes (job_id TEXT PRIMARY KEY,body TEXT NOT NULL,
 revision INTEGER NOT NULL,updated TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS saved_views (id TEXT PRIMARY KEY,name TEXT NOT NULL,name_key TEXT UNIQUE NOT NULL,
 search TEXT NOT NULL,status TEXT NOT NULL,sort TEXT NOT NULL,created TEXT NOT NULL,updated TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS employer_accounts (id TEXT PRIMARY KEY, origin TEXT NOT NULL,
 company TEXT NOT NULL, state TEXT NOT NULL, updated TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS model_requests (id INTEGER PRIMARY KEY, timestamp TEXT NOT NULL, run_id TEXT NOT NULL, provider TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS model_requests_time ON model_requests(timestamp);
CREATE INDEX IF NOT EXISTS model_requests_run ON model_requests(run_id);
CREATE TABLE IF NOT EXISTS model_request_metadata (request_id INTEGER PRIMARY KEY REFERENCES model_requests(id), model TEXT NOT NULL, effort TEXT NOT NULL, duration REAL NOT NULL, success INTEGER NOT NULL, usage TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS model_limit_override (id INTEGER PRIMARY KEY CHECK(id=1), expires REAL NOT NULL, previous TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS remote_draft_steps (id TEXT PRIMARY KEY, job_id TEXT NOT NULL, tenant TEXT NOT NULL, step TEXT NOT NULL, request_hash TEXT NOT NULL, sources_hash TEXT NOT NULL, state TEXT NOT NULL, created TEXT NOT NULL, updated TEXT NOT NULL, acknowledgement TEXT NOT NULL DEFAULT '');
CREATE INDEX IF NOT EXISTS remote_draft_job ON remote_draft_steps(job_id,created);
CREATE TABLE IF NOT EXISTS adapter_verifications (tenant TEXT NOT NULL, signature TEXT NOT NULL, version INTEGER NOT NULL, application_id TEXT NOT NULL, verified TEXT NOT NULL, PRIMARY KEY(tenant,signature,version));
CREATE TABLE IF NOT EXISTS worker_control (id INTEGER PRIMARY KEY CHECK(id=1), generation INTEGER NOT NULL);
INSERT OR IGNORE INTO worker_control VALUES(1,0);
CREATE TABLE IF NOT EXISTS config (id INTEGER PRIMARY KEY CHECK(id=1), value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS facts (key TEXT PRIMARY KEY, value TEXT NOT NULL, source TEXT NOT NULL,
 confirmed INTEGER NOT NULL, revision INTEGER NOT NULL, updated TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS templates (id TEXT PRIMARY KEY, category TEXT NOT NULL, body TEXT NOT NULL, revision INTEGER NOT NULL, updated TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS answers (id TEXT PRIMARY KEY, question TEXT NOT NULL, host TEXT NOT NULL,
 options TEXT NOT NULL, value TEXT NOT NULL, fact_key TEXT, revision INTEGER NOT NULL, updated TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS writing_answers (id TEXT PRIMARY KEY, body TEXT NOT NULL, provenance TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS field_bindings (id TEXT PRIMARY KEY, host TEXT NOT NULL, label TEXT NOT NULL,
 options TEXT NOT NULL, fact_key TEXT, template_id TEXT, created TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS verification_challenges (application_id TEXT PRIMARY KEY, provider TEXT NOT NULL,
 url TEXT NOT NULL, company TEXT NOT NULL, requested REAL NOT NULL, code_length INTEGER NOT NULL,
 state TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS report_outbox (id TEXT PRIMARY KEY, recipient TEXT NOT NULL, subject TEXT NOT NULL,
 body TEXT NOT NULL, message_id TEXT NOT NULL UNIQUE, state TEXT NOT NULL, created TEXT NOT NULL,
 attempts INTEGER NOT NULL, provider_id TEXT, sent TEXT, last_error TEXT);
CREATE TABLE IF NOT EXISTS mail_consumptions (message_id TEXT PRIMARY KEY, challenge_id TEXT NOT NULL,
 consumed REAL NOT NULL);
CREATE TABLE IF NOT EXISTS materials (id TEXT PRIMARY KEY, hash TEXT NOT NULL, filename TEXT NOT NULL,
 original_name TEXT NOT NULL, media_type TEXT NOT NULL, kind TEXT NOT NULL, text TEXT NOT NULL,
 confirmed INTEGER NOT NULL, role TEXT NOT NULL, revision INTEGER NOT NULL, created TEXT NOT NULL,
 updated TEXT NOT NULL, UNIQUE(hash,kind));
CREATE TABLE IF NOT EXISTS documents (kind TEXT PRIMARY KEY, hash TEXT NOT NULL, filename TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS generated_documents (job_id TEXT NOT NULL, kind TEXT NOT NULL,
 hash TEXT NOT NULL, filename TEXT NOT NULL, fingerprint TEXT NOT NULL, provenance TEXT NOT NULL,
 created TEXT NOT NULL, PRIMARY KEY(job_id,kind));
CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, company TEXT NOT NULL, company_key TEXT NOT NULL,
 title TEXT NOT NULL, url TEXT UNIQUE NOT NULL, host TEXT NOT NULL, source TEXT NOT NULL,
 payload TEXT NOT NULL, score INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'discovered',
 reason TEXT NOT NULL DEFAULT '', first_seen TEXT NOT NULL, updated TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS job_decisions (job_id TEXT PRIMARY KEY, decision TEXT NOT NULL,
 previous_status TEXT NOT NULL, created TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS applications (id TEXT PRIMARY KEY, job_id TEXT NOT NULL UNIQUE,
 company_key TEXT NOT NULL, state TEXT NOT NULL, package TEXT NOT NULL, hash TEXT NOT NULL,
 created TEXT NOT NULL, updated TEXT NOT NULL, attempted TEXT, confirmation TEXT, screenshot TEXT);
CREATE TABLE IF NOT EXISTS questions (id TEXT PRIMARY KEY, job_id TEXT NOT NULL, host TEXT NOT NULL,
 label TEXT NOT NULL, options TEXT NOT NULL, reason TEXT NOT NULL, resolved INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS events (seq INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT NOT NULL,
 kind TEXT NOT NULL, subject TEXT NOT NULL, detail TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sources (id TEXT PRIMARY KEY, status TEXT NOT NULL, checked TEXT NOT NULL,
 error TEXT NOT NULL DEFAULT '', payload TEXT NOT NULL DEFAULT '{}');
CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, started TEXT NOT NULL, finished TEXT,
 status TEXT NOT NULL, submitted INTEGER NOT NULL DEFAULT 0, detail TEXT NOT NULL DEFAULT '');
CREATE INDEX IF NOT EXISTS applications_company_state ON applications(company_key,state);
CREATE INDEX IF NOT EXISTS applications_state_attempted ON applications(state,attempted);
CREATE INDEX IF NOT EXISTS jobs_recent ON jobs(first_seen DESC,id);
CREATE INDEX IF NOT EXISTS jobs_fit ON jobs(score DESC,first_seen DESC,id);
CREATE INDEX IF NOT EXISTS questions_unresolved_job ON questions(resolved,job_id);
CREATE INDEX IF NOT EXISTS events_kind_subject_seq ON events(kind,subject,seq DESC);
CREATE INDEX IF NOT EXISTS employer_accounts_state ON employer_accounts(state);
CREATE INDEX IF NOT EXISTS runs_status_started ON runs(status,started);
"""

APPLICATION_METADATA = 'id,job_id,company_key,state,hash,created,updated,attempted,confirmation,screenshot'


class Store:
    def __init__(self, root: Path):
        self.root = private_dir(root)
        self.path = self.root / "ledger.sqlite3"
        if self.path.is_symlink():
            raise ValueError("Ledger must not be a symlink")
        self.db = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        try:
            try:metadata=self.db.execute('SELECT fingerprint,schema_version FROM schema_metadata WHERE id=1').fetchone()
            except sqlite3.OperationalError as error:
                if not any(message in str(error) for message in ('no such table: schema_metadata','no such column: schema_version')):raise
                metadata=None
            fingerprint=digest(SCHEMA)
            if (not metadata or metadata['fingerprint']!=fingerprint
                    or metadata['schema_version']!=self.db.execute('PRAGMA schema_version').fetchone()[0]):
                # Record readiness only after every idempotent schema statement
                # and the initial config succeed. Ordinary readers need no DDL
                # or INSERT lock while the worker is writing.
                self.db.executescript(SCHEMA)
                if 'schema_version' not in {row['name'] for row in self.db.execute('PRAGMA table_info(schema_metadata)')}:
                    self.db.execute('ALTER TABLE schema_metadata ADD COLUMN schema_version INTEGER NOT NULL DEFAULT 0')
                self.db.execute("INSERT OR IGNORE INTO config VALUES (1, ?)", (json.dumps(DEFAULTS),))
                self.db.execute('INSERT OR REPLACE INTO schema_metadata(id,fingerprint,schema_version) VALUES(1,?,?)',
                                (fingerprint,self.db.execute('PRAGMA schema_version').fetchone()[0]))
            elif not self.db.execute('SELECT 1 FROM config WHERE id=1').fetchone():
                self.db.execute('INSERT OR IGNORE INTO config VALUES(1,?)',(json.dumps(DEFAULTS),))
            os.chmod(self.path, 0o600)
        except BaseException:
            self.db.close();raise

    def close(self):
        self.db.close()

    @contextlib.contextmanager
    def transaction(self):
        nested=self.db.in_transaction
        savepoint='hireme_'+uuid.uuid4().hex if nested else None
        self.db.execute('SAVEPOINT '+savepoint if nested else 'BEGIN IMMEDIATE')
        try:
            yield
            self.db.execute('RELEASE SAVEPOINT '+savepoint if nested else 'COMMIT')
        except BaseException:
            self.db.execute('ROLLBACK TO SAVEPOINT '+savepoint if nested else 'ROLLBACK')
            if nested:self.db.execute('RELEASE SAVEPOINT '+savepoint)
            raise

    def settings(self):
        override=self.db.execute('SELECT expires,previous FROM model_limit_override WHERE id=1').fetchone()
        if override and time.time()>=override['expires']:
            with self.transaction():
                override=self.db.execute('SELECT expires,previous FROM model_limit_override WHERE id=1').fetchone()
                if override and time.time()>=override['expires']:
                    saved=json.loads(self.db.execute('SELECT value FROM config').fetchone()[0])
                    saved.update(json.loads(override['previous']))
                    self.db.execute('UPDATE config SET value=? WHERE id=1',(json.dumps(saved),))
                    self.db.execute('DELETE FROM model_limit_override WHERE id=1')
                    self.event('model_limits_restored','config',json.loads(override['previous']))
        return validate_settings(json.loads(self.db.execute("SELECT value FROM config").fetchone()[0]))

    def temporary_model_limits(self,daily=500,cycle=200,seconds=21600):
        if type(seconds) is not int or not 1<=seconds<=86400:raise ValueError('Temporary limits require an expiry within 24 hours')
        with self.transaction():
            saved=self.settings()
            if self.db.execute('SELECT 1 FROM model_limit_override').fetchone():raise ValueError('Restore the active temporary limit first')
            previous={k:saved[k] for k in ('max_model_requests_per_day','max_model_requests_per_cycle')}
            updated=validate_settings({'max_model_requests_per_day':daily,'max_model_requests_per_cycle':cycle},saved)
            self.db.execute('INSERT INTO model_limit_override VALUES(1,?,?)',(time.time()+seconds,json.dumps(previous)))
            self.db.execute('UPDATE config SET value=? WHERE id=1',(json.dumps(updated),))
            self.event('model_limits_temporarily_raised','config',{'daily':daily,'cycle':cycle,'expires_in':seconds})

    def restore_model_limits(self):
        self.db.execute('UPDATE model_limit_override SET expires=0 WHERE id=1')
        return self.settings()

    def control_generation(self):
        return self.db.execute('SELECT generation FROM worker_control WHERE id=1').fetchone()[0]

    def checkpoint(self):
        preparation_generation=getattr(self,'preparation_generation',None)
        if preparation_generation is not None and preparation_generation!=self.control_generation():
            raise Blocked('paused')
        discovery_generation=getattr(self,'discovery_generation',None)
        if discovery_generation is not None and discovery_generation!=self.control_generation():
            raise Blocked('paused')
        generation=getattr(self,'run_generation',None)
        if generation is not None and (generation!=self.control_generation() or not self.settings()['live_enabled']):
            raise Blocked('paused')
        deadline=getattr(self,'run_deadline',None)
        if deadline is not None and time.monotonic()>=deadline:
            raise Blocked('cycle_timeout','The batch time budget was reached')

    def reserve_model_request(self):
        if self.db.in_transaction:raise Blocked('model_request_transaction','Reserve model requests outside applicant-data transactions')
        with self.transaction():
            self.checkpoint()
            s=self.settings();rid=getattr(self,'active_run_id',None) or 'setup:'+datetime.now(ZoneInfo(s['timezone'])).date().isoformat()
            today=datetime.now(ZoneInfo(s['timezone'])).date()
            daily=sum(datetime.fromisoformat(r[0]).astimezone(ZoneInfo(s['timezone'])).date()==today for r in self.db.execute('SELECT timestamp FROM model_requests WHERE timestamp>=?',((datetime.now(timezone.utc)-timedelta(days=2)).isoformat(),)))
            cycle=self.db.execute('SELECT count(*) FROM model_requests WHERE run_id=?',(rid,)).fetchone()[0]
            if daily>=s['max_model_requests_per_day'] or cycle>=s['max_model_requests_per_cycle']:raise Blocked('model_budget_exhausted','Wait for the next batch/day or change your request limits')
            self.db.execute('INSERT INTO model_requests(timestamp,run_id,provider) VALUES(?,?,?)',(now(),rid,s['provider']))
            return self.db.execute('SELECT last_insert_rowid()').fetchone()[0]

    def event(self, kind, subject, detail):
        self.db.execute("INSERT INTO events(timestamp,kind,subject,detail) VALUES(?,?,?,?)",
                        (now(), kind, str(subject), json.dumps(detail, ensure_ascii=False)))

    def update_settings(self, changes):
        with self.transaction():
            s = validate_settings(changes, self.settings())
            if s["live_enabled"] and (not s["onboarding_complete"] or self.missing_setup()):
                raise ValueError("Finish confirmed onboarding before enabling submissions")
            if changes.get("live_enabled") is False:
                self.db.execute("UPDATE worker_control SET generation=generation+1 WHERE id=1")
                self.event("pause_requested", "worker", {})
            self.db.execute("UPDATE config SET value=? WHERE id=1", (json.dumps(s),))
            self.event("settings_updated", "config", changes)
        self.export_config()
        return s

    def facts(self, confirmed=True):
        rows = self.db.execute("SELECT * FROM facts" + (" WHERE confirmed=1" if confirmed else ""))
        return {r["key"]: dict(r) for r in rows}

    def put_facts(self, values, source="user", confirmed=True, clear_keys=None):
        # A dashboard submission is an explicit user confirmation, not model approval.
        if not isinstance(values, dict):
            raise ValueError("Provide a facts object")
        clear_keys = [] if clear_keys is None else clear_keys
        if not isinstance(clear_keys, list) or any(
                not isinstance(key, str) or key not in FACTS for key in clear_keys):
            raise ValueError("Choose known facts to clear")
        cleared = set(clear_keys)
        values = {key: validate_fact(key, value) for key, value in values.items()
                  if value is not None and value != ""}
        if cleared & values.keys():
            raise ValueError("A fact cannot be saved and cleared at the same time")
        with self.transaction():
            old = self.facts(False)
            history = bool(self.db.execute("SELECT 1 FROM applications LIMIT 1").fetchone()
                           or self.db.execute("SELECT 1 FROM employer_accounts LIMIT 1").fetchone())
            if old.get("email", {}).get("confirmed") and history:
                if "email" in cleared:
                    raise ValueError("Applicant identity cannot be cleared after application or account history exists")
                if "email" in values and values["email"] != old["email"]["value"]:
                    raise ValueError("Applicant identity cannot change after application or account history exists")
            for key, value in values.items():
                self.db.execute("""INSERT INTO facts VALUES(?,?,?,?,?,?) ON CONFLICT(key) DO UPDATE SET
                    value=excluded.value,source=excluded.source,confirmed=excluded.confirmed,
                    revision=facts.revision+1,updated=excluded.updated""",
                    (key, value, source, int(confirmed), 1, now()))
            for key in cleared:
                # Preserve revisions so revoked facts cannot be mistaken for old confirmations.
                self.db.execute("""UPDATE facts SET value='',source='revoked',confirmed=0,
                    revision=revision+1,updated=? WHERE key=?""", (now(), key))
            if cleared:
                self.event("facts_revoked", "profile", sorted(cleared))
                if cleared & REQUIRED and self.settings()['live_enabled']:
                    settings = self.settings()
                    settings['live_enabled'] = False
                    self.db.execute("UPDATE config SET value=? WHERE id=1", (json.dumps(settings),))
                    self.db.execute("UPDATE worker_control SET generation=generation+1 WHERE id=1")
                    self.event("pause_requested", "worker", {'reason': 'required_fact_removed'})
            self.event("facts_confirmed" if confirmed else "facts_proposed", "profile", sorted(values))
            self.discard_prepared()
        if not self.db.in_transaction:self.export_config()

    def export_config(self):
        atomic_json(self.root / "config" / "profile.json", {k:v["value"] for k,v in self.facts().items()})
        atomic_json(self.root / "config" / "settings.json", self.settings())
        atomic_json(self.root / "config" / "answers.json", [dict(x) for x in self.db.execute("SELECT * FROM answers")])

    def discard_prepared(self,job_ids=None):
        """Caller owns a transaction. Reset only jobs whose unattempted draft is removed."""
        def group(ids=None):
            clause="state='prepared'";parameters=[]
            if ids is not None:
                clause+=' AND job_id IN ('+','.join('?' for _ in ids)+')';parameters.extend(ids)
            self.db.execute("UPDATE jobs SET status='discovered',reason='',updated=? WHERE status='prepared' AND id IN (SELECT job_id FROM applications WHERE "+clause+')',(now(),*parameters))
            return self.db.execute('DELETE FROM applications WHERE '+clause,parameters).rowcount
        if job_ids is None:return group()
        identifiers=iter(job_ids);count=0
        size=min(500,max(1,self.db.getlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER)-1))
        while batch:=list(islice(identifiers,size)):count+=group(batch)
        return count

    def missing_setup(self):
        missing = sorted(REQUIRED - self.facts().keys())
        if not self.document_available('resume'):
            missing.append("resume")
        f = self.facts()
        if all(k in f for k in ("earliest_start", "latest_start")) and f["earliest_start"]["value"] > f["latest_start"]["value"]:
            missing.append("valid start window")
        if self.db.execute("SELECT 1 FROM questions WHERE reason='legacy_history_review' AND resolved=0").fetchone():
            missing.append("legacy history review")
        return missing

    def document_available(self,kind):
        row=self.db.execute('SELECT hash,filename FROM documents WHERE kind=?',(kind,)).fetchone()
        if not row or not isinstance(row['hash'],str) or not isinstance(row['filename'],str) or not re.fullmatch(r'[a-f0-9]{64}',row['hash']) or row['filename']!=row['hash']+'.pdf':return False
        parent=self.root/'documents';path=parent/row['filename']
        try:
            if parent.is_symlink() or path.is_symlink() or not path.is_file():return False
            # Availability only: full document hashing still happens before upload.
            with path.open('rb') as document:return document.read(5)==b'%PDF-'
        except OSError:return False

    @staticmethod
    def question_key(host, label, options):
        return digest([host.lower(), " ".join(label.casefold().split()), options])

    def ask(self, job_id, host, label, options, reason="missing_fact", *, field=None, context=None):
        if field is not None:
            from .field_context import field_context
            metadata=field_context(host,field,context)
            key=digest(['approved_answer',metadata])
            self.db.execute('INSERT OR REPLACE INTO question_contexts VALUES(?,?)',(key,json.dumps(metadata)))
        else:key = self.question_key(host, label, options)
        self.db.execute("INSERT INTO questions VALUES(?,?,?,?,?,?,0) ON CONFLICT(id) DO UPDATE SET job_id=excluded.job_id,reason=excluded.reason,resolved=0",
                        (key, job_id, host, label, json.dumps(options), reason))
        return key

    def retire_questions(self, job_id, keep):
        """Hide a job's unanswered questions that its latest attempt did not ask again."""
        keep=sorted(keep)
        self.db.execute('UPDATE questions SET resolved=1 WHERE job_id=? AND resolved=0 AND reason!=?'
                        +(' AND id NOT IN ('+','.join('?' for _ in keep)+')' if keep else ''),(job_id,'legacy_history_review',*keep))

    def answer_question(self, qid, value, fact_key=None):
        q = self.db.execute("SELECT * FROM questions WHERE id=?", (qid,)).fetchone()
        if not q:
            raise ValueError("Question does not exist")
        if not isinstance(value,str) or not value.strip() or len(value)>12000:
            raise ValueError("A nonempty answer is required")
        options = json.loads(q["options"])
        if options and value not in options:
            raise ValueError("Choose an exact option")
        if fact_key:
            f = self.facts().get(fact_key)
            if not f or f["value"] != value:
                raise ValueError("Answer must equal the confirmed fact")
        with self.transaction():
            self.db.execute("""INSERT INTO answers VALUES(?,?,?,?,?,?,1,?) ON CONFLICT(id) DO UPDATE SET
                value=excluded.value,fact_key=excluded.fact_key,revision=answers.revision+1,updated=excluded.updated""",
                (qid,q["label"],q["host"],q["options"],value,fact_key,now()))
            self.db.execute("UPDATE questions SET resolved=1 WHERE id=?", (qid,))
            self.db.execute("""UPDATE jobs SET status='discovered',reason='',updated=? WHERE status='blocked' AND id IN
                (SELECT job_id FROM questions WHERE id=?)""", (now(),qid))
            self.event("answer_saved",qid,{"fact_key":fact_key})
        self.export_config()

    def put_template(self, category, body, tid=None):
        if category not in ("motivation", "project", "experience") or not isinstance(body,str) or not 20<=len(body)<=12000:
            raise ValueError("Choose a template category and 20–12000 characters of confirmed text")
        tid=tid or uuid.uuid4().hex
        self.db.execute("INSERT INTO templates VALUES(?,?,?,1,?) ON CONFLICT(id) DO UPDATE SET category=excluded.category,body=excluded.body,revision=templates.revision+1,updated=excluded.updated",(tid,category,body,now()))
        self.event("template_confirmed",tid,{"category":category})
        return tid

    def _user_template(self, tid):
        if not isinstance(tid, str) or not tid or len(tid) > 100:
            raise ValueError("Choose an existing approved wording")
        if tid.startswith('material:'):
            raise ValueError("Edit or revoke this source in Writing & context")
        row = self.db.execute('SELECT * FROM templates WHERE id=?', (tid,)).fetchone()
        if not row:
            raise ValueError("Approved wording not found")
        return row

    def edit_template(self, tid, category, body):
        with self.transaction():
            self._user_template(tid)
            self.put_template(category, body, tid)
            self.discard_prepared()
        return tid

    def revoke_template(self, tid):
        with self.transaction():
            row = self._user_template(tid)
            self.db.execute('DELETE FROM templates WHERE id=?', (tid,))
            self.discard_prepared()
            self.event('template_revoked', tid, {'category': row['category']})

    def templates(self):
        return [dict(x) for x in self.db.execute("SELECT * FROM templates")]

    def saved_answer(self, host, label, options, *, field=None, context=None):
        if field is not None:
            from .field_context import field_context, approval_context, MAPPING_VERSION
            scoped=digest(['approved_answer',field_context(host,field,context)])
            r=self.db.execute('SELECT * FROM answers WHERE id=?',(scoped,)).fetchone()
            if r:return dict(r)
            current=field_context(host,field,context)
            comparison=digest(approval_context(current))
            for candidate in self.db.execute('SELECT a.*,q.context FROM answers a JOIN question_contexts q ON q.id=a.id WHERE a.host=? ORDER BY a.updated DESC,a.id',(host,)):
                try:previous=json.loads(candidate['context'])
                except (ValueError,TypeError):continue
                if isinstance(previous,dict) and previous.get('version') in range(3,MAPPING_VERSION+1) and digest(approval_context(previous))==comparison:
                    # Rule updates invalidate model bindings, not an unchanged
                    # explicit user approval. Resolve still validates its value.
                    result=dict(candidate);result.pop('context');return result
        qid = self.question_key(host,label,options)
        r = self.db.execute("SELECT * FROM answers WHERE id=?",(qid,)).fetchone()
        if not r and '|' in host:
            # User-linked universal facts can be reused for identical wording/options at the same ATS.
            universal={'full_name','first_name','last_name','email','phone','location','street','city','state','postal_code','country','linkedin','github','website','school','high_school','major','graduation','gpa','work_authorized_us','needs_sponsorship','citizenship','us_person','unrestricted_authorization','race','gender','veteran','disability','professional_years'}
            for candidate in self.db.execute("SELECT * FROM answers WHERE question=? AND options=? AND fact_key IS NOT NULL",(label,json.dumps(options))):
                if candidate['host'].split('|',1)[0]==host.split('|',1)[0] and candidate['fact_key'] in universal:
                    r=candidate;break
        return dict(r) if r else None

    def writing_answer(self, host, label, options):
        row=self.db.execute('SELECT * FROM writing_answers WHERE id=?',(self.question_key(host,label,options),)).fetchone()
        return {'value':row['body'],'provenance':json.loads(row['provenance'])} if row else None

    def save_writing_answer(self, host, label, options, answer):
        self.db.execute('INSERT OR REPLACE INTO writing_answers VALUES(?,?,?)',
                        (self.question_key(host,label,options),answer['value'],json.dumps(answer['provenance'])))

    def field_binding(self, host, label, options):
        row=self.db.execute('SELECT * FROM field_bindings WHERE id=?',(self.question_key(host,label,options),)).fetchone()
        return dict(row) if row else None

    def bind_field(self, host, label, options, fact_key=None, template_id=None):
        if bool(fact_key)==bool(template_id):raise ValueError('Choose one supported source')
        if fact_key and fact_key not in self.facts():raise ValueError('Unconfirmed fact')
        if template_id and not any(t['id']==template_id for t in self.templates()):raise ValueError('Unknown template')
        self.db.execute('INSERT OR REPLACE INTO field_bindings VALUES(?,?,?,?,?,?,?)',
                        (self.question_key(host,label,options),host,label,json.dumps(options),fact_key,template_id,now()))

    def resolve_known_question(self, host, label, options=None, *, field=None, context=None):
        if field is not None:
            from .field_context import field_context, approval_context, MAPPING_VERSION
            metadata=field_context(host,field,context)
            identifiers=[digest(['approved_answer',metadata])]
            comparison=digest(approval_context(metadata))
            for row in self.db.execute('SELECT q.id,q.job_id,q.label,c.context FROM questions q LEFT JOIN question_contexts c ON c.id=q.id WHERE (q.host=? OR q.job_id=?) AND q.resolved=0',(host,(context or {}).get('id'))):
                if row['context'] is None:
                    legacy_label=' '.join(row['label'].casefold().split()).rstrip(' *?:')
                    # These unique profile controls had dynamic option lists in
                    # old forms. Retire their obsolete queue entries only after
                    # the current control resolves; never import an approval.
                    if (row['job_id']==(context or {}).get('id') and legacy_label==metadata['label']
                            and legacy_label in {'school','degree'} and not metadata.get('section_entry',0)):
                        identifiers.append(row['id'])
                    continue
                try:previous=json.loads(row['context'])
                except (ValueError,TypeError):continue
                same_field=(isinstance(previous,dict) and row['job_id']==(context or {}).get('id')
                            and previous.get('label')==metadata['label']
                            and previous.get('section','')==metadata['section']
                            and previous.get('section_entry',0)==metadata.get('section_entry',0))
                corrected_sms=(isinstance(previous,dict) and row['job_id']==(context or {}).get('id')
                               and metadata['label']=='consent to receiving text messages' and previous.get('label')=='phone'
                               and previous.get('widget')==metadata['widget']=='radio'
                               and previous.get('options')==[list(pair) for pair in metadata['options']])
                if isinstance(previous,dict) and previous.get('version') in range(3,MAPPING_VERSION+1) and (same_field or corrected_sms or digest(approval_context(previous))==comparison):
                    # A successfully resolved current field retires its obsolete
                    # question for this job. Saved approvals retain strict scope.
                    identifiers.append(row['id'])
            # Unscoped historical dates cannot establish which section was reviewed.
            if not field.get('section'):identifiers.append(self.question_key(host,label,options or []))
            self.db.execute('UPDATE questions SET resolved=1 WHERE id IN ('+','.join('?' for _ in identifiers)+') AND reason!=?',(*identifiers,'legacy_history_review'))
        else:
            self.db.execute('UPDATE questions SET resolved=1 WHERE host=? AND label=? AND reason!=?',(host,label,'legacy_history_review'))


    def upsert_job(self, job):
        # A requisition URL owns its durable identity, even if an imported record
        # supplies another ID. Refresh display metadata alongside its payload;
        # recorded application keys and outcome states remain evidence.
        with self.transaction():
            existing=self.db.execute('SELECT id,payload FROM jobs WHERE url=?',(job['url'],)).fetchone()
            key=existing['id'] if existing else job['id']
            payload={**job,'id':key}
            if job.get('answer_scope') and existing:
                previous=json.loads(existing['payload'])
                payload['_listing_hash']=previous.get('_listing_hash') or digest({k:previous.get(k,'') for k in ('url','company','title','location','description')})
            else:
                payload['_listing_hash']=digest({k:job.get(k,'') for k in ('url','company','title','location','description')})
            ck=self.company(job['company'])
            self.db.execute("""INSERT INTO jobs(id,company,company_key,title,url,host,source,payload,first_seen,updated)
             VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(url) DO UPDATE SET
             company=excluded.company,company_key=excluded.company_key,title=excluded.title,
             host=excluded.host,source=excluded.source,payload=excluded.payload,updated=excluded.updated""",
              (key,job['company'],ck,job['title'],job['url'],job['host'],job['source'],json.dumps(payload),now(),now()))
        return key

    def company(self, name, settings=None):
        s=self.settings() if settings is None else settings
        return company_normalizer(s['company_aliases'])(name)

    def decide_job(self, jid, decision):
        if decision not in ('manually_applied','skipped','undo'):
            raise ValueError('Choose applied manually, do not apply, or undo')
        with self.transaction():
            job=self.db.execute('SELECT * FROM jobs WHERE id=?',(jid,)).fetchone()
            if not job:raise ValueError('Job not found')
            app=self.db.execute('SELECT state FROM applications WHERE job_id=?',(jid,)).fetchone()
            if app and app[0] in ('submitting','unknown','awaiting_verification','confirmed'):
                raise ValueError('This application already has a submission record; reconcile uncertain outcomes first')
            previous=self.db.execute('SELECT * FROM job_decisions WHERE job_id=?',(jid,)).fetchone()
            if decision=='undo':
                if not previous:raise ValueError('No manual decision to undo')
                self.db.execute('DELETE FROM job_decisions WHERE job_id=?',(jid,))
                self.db.execute("UPDATE jobs SET status='discovered',reason='',updated=? WHERE id=?",(now(),jid))
            else:
                self.db.execute("INSERT INTO job_decisions VALUES(?,?,?,?) ON CONFLICT(job_id) DO UPDATE SET decision=excluded.decision,created=excluded.created",
                    (jid,decision,job['status'],now()))
                self.db.execute("UPDATE jobs SET status=?,reason='',updated=? WHERE id=?",(decision,now(),jid))
            self.event('job_decision',jid,{'decision':decision})

    def check_job_decision(self, jid):
        decision=self.db.execute('SELECT decision FROM job_decisions WHERE job_id=?',(jid,)).fetchone()
        if decision:raise Blocked(decision[0])

    def block(self, jid, reason, detail=""):
        self.db.execute("UPDATE jobs SET status='blocked',reason=?,updated=? WHERE id=? AND id NOT IN (SELECT job_id FROM job_decisions)",(reason + (": " + detail if detail else ""),now(),jid))
        self.event("blocked",jid,{"reason":reason,"detail":detail})

    def application_history(self, settings=None, states=None):
        """Narrow history rows with current and originally recorded company identities.

        Alias edits must not hide prior attempts. Stored keys remain evidence; current
        aliases and the employer name on the original posting supplement those keys.
        """
        settings = self.settings() if settings is None else settings
        normalize = company_normalizer(settings['company_aliases'])
        where = '' if states is None else ' WHERE a.state IN (' + ','.join('?' for _ in states) + ')'
        rows = self.db.execute('''SELECT a.id,a.job_id,a.company_key,a.state,a.created,a.attempted,j.company
            FROM applications a LEFT JOIN jobs j ON j.id=a.job_id''' + where, () if states is None else states)
        history = []
        for row in rows:
            entry = dict(row)
            keys = {row['company_key'], normalize(row['company_key'])}
            if row['company']: keys.add(normalize(row['company']))
            entry['company_keys'] = keys
            history.append(entry)
        return history

    def _check_budget(self, job, s):
        self.check_job_decision(job["id"])
        ck=self.company(job["company"],s)
        if ck in {self.company(x,s) for x in s["skip_companies"]+s["interview_companies"]}:
            raise Blocked("company_blocked")
        all_active=self.application_history(s,('submitting','unknown','confirmed','awaiting_verification'))
        manual=[{'state':'manually_applied','created':r['created'],'attempted':r['created'],
                 'company_keys':{self.company(r['company'],s)}}
                for r in self.db.execute("SELECT d.created,j.company FROM job_decisions d JOIN jobs j ON j.id=d.job_id WHERE d.decision='manually_applied'")]
        all_active.extend(manual)
        active=[row for row in all_active if ck in row['company_keys']]
        if any(r["state"]=="awaiting_verification" for r in active):
            raise Blocked("company_verification_pending","Complete the earlier application verification first")
        if any(r["state"] in ("submitting","unknown") for r in active):
            raise Blocked("company_uncertain","Reconcile the earlier attempt first")
        spam_ids={row[0] for row in self.db.execute("SELECT id FROM applications WHERE state='not_submitted' AND LOWER(confirmation) LIKE '%possible spam%'")}
        if any(row['id'] in spam_ids and ck in row['company_keys'] for row in self.application_history(s,('not_submitted',))):
            raise Blocked('company_submission_rejected','An earlier submission was rejected as possible spam; complete manually or explicitly review the earlier attempt')
        if len(active)>=s["max_per_company"]:
            raise Blocked("company_limit")
        local_day=datetime.now(ZoneInfo(s["timezone"])).date()
        daily=[r for r in all_active if datetime.fromisoformat(r["attempted"] or r["created"]).astimezone(ZoneInfo(s["timezone"])).date()==local_day]
        if len(daily)>=s["max_per_day"]:
            raise Blocked("daily_limit")
        if any(ck in r['company_keys'] for r in daily):
            raise Blocked("company_same_day")
        cutoff=datetime.now(timezone.utc)-timedelta(days=s["company_cooldown_days"])
        if any(datetime.fromisoformat(r["attempted"] or r["created"])>cutoff for r in active):
            raise Blocked("company_cooldown")

    def prepare(self, job, package):
        from .answers import validate_package
        validate_package(self,job,package)
        with self.transaction():
            s=self.settings()
            self._check_budget(job,s)
            prev=self.db.execute("SELECT state FROM applications WHERE job_id=?",(job["id"],)).fetchone()
            if prev and prev[0] != "prepared":
                raise Blocked("duplicate_or_uncertain")
            aid=digest([job["id"], package])
            self.db.execute("DELETE FROM applications WHERE job_id=? AND state='prepared'",(job["id"],))
            self.db.execute("INSERT INTO applications(id,job_id,company_key,state,package,hash,created,updated) VALUES(?,?,?,'prepared',?,?,?,?)",
                (aid,job["id"],self.company(job["company"]),json.dumps(package),digest(package),now(),now()))
            self.event("prepared",aid,{"package_hash":digest(package)})
        return aid

    def begin_submit(self, aid):
        from .answers import validate_package
        from .policy import eligible
        with self.transaction():
            self.checkpoint()
            app=self.db.execute("SELECT * FROM applications WHERE id=?",(aid,)).fetchone()
            if not app or app["state"]!="prepared":
                raise Blocked("invalid_transition")
            s=self.settings()
            if not s["live_enabled"] or not s["onboarding_complete"] or self.missing_setup():
                raise Blocked("not_ready")
            job=json.loads(self.db.execute("SELECT payload FROM jobs WHERE id=?",(app["job_id"],)).fetchone()[0])
            package=json.loads(app["package"])
            if digest(package)!=app["hash"]:
                raise Blocked("package_tampered")
            eligible(job,s,self.facts())
            validate_package(self,job,package)
            self._check_budget(job,s)
            self.db.execute("UPDATE applications SET state='submitting',attempted=?,updated=? WHERE id=?",(now(),now(),aid))
            self.event("submit_intent",aid,{"hash":app["hash"]})
        return package

    def begin_verification(self,aid):
        with self.transaction():
            self.checkpoint()
            if not self.settings()['live_enabled']:raise Blocked('paused')
            app=self.db.execute('SELECT state FROM applications WHERE id=?',(aid,)).fetchone()
            challenge=self.db.execute('SELECT * FROM verification_challenges WHERE application_id=?',(aid,)).fetchone()
            if not app or app[0]!='awaiting_verification' or not challenge or challenge['state']!='pending' or challenge['attempts']>=1:
                raise Blocked('verification_not_ready')
            self.db.execute("UPDATE applications SET state='submitting',updated=? WHERE id=?",(now(),aid))
            self.db.execute("UPDATE verification_challenges SET state='verifying',attempts=attempts+1 WHERE application_id=?",(aid,))
            self.event('verification_intent',aid,{})

    def finish(self, aid, state, confirmation="", screenshot=""):
        if state not in ("confirmed","unknown","awaiting_verification","not_submitted"):
            raise ValueError("Invalid outcome")
        with self.transaction():
            r=self.db.execute("SELECT state,job_id FROM applications WHERE id=?",(aid,)).fetchone()
            if not r or r[0]!="submitting":
                raise Blocked("invalid_transition")
            self.db.execute("UPDATE applications SET state=?,updated=?,confirmation=?,screenshot=? WHERE id=?",
                            (state,now(),confirmation[-4000:],screenshot,aid))
            self.db.execute("UPDATE jobs SET status=?,reason=?,updated=? WHERE id=?",
                            (state,"" if state=="confirmed" else "Employer rejected submission; manual action required" if state=='not_submitted' else "Email verification required; application not yet submitted" if state=="awaiting_verification" else "Submission outcome needs reconciliation",now(),r[1]))
            self.db.execute("UPDATE verification_challenges SET state=? WHERE application_id=?",('complete' if state=='confirmed' else 'held' if state=='unknown' else 'pending',aid))
            self.event(state,aid,{"confirmation":confirmation[-4000:],"screenshot":screenshot})

    def recover(self):
        with self.transaction():
            self.db.execute("UPDATE employer_accounts SET state='uncertain',updated=? WHERE state IN ('creating','signing_in')",(now(),))
            rows=list(self.db.execute("SELECT id,job_id FROM applications WHERE state='submitting'"))
            for r in rows:
                self.db.execute("UPDATE applications SET state='unknown',updated=? WHERE id=?",(now(),r[0]))
                self.db.execute("UPDATE jobs SET status='unknown',reason='Worker stopped after submit intent',updated=? WHERE id=?",(now(),r[1]))
                self.db.execute("UPDATE verification_challenges SET state='held' WHERE application_id=?",(r[0],))
                self.event("crash_recovered",r[0],{"state":"unknown"})
            self.db.execute("UPDATE runs SET status='interrupted',finished=? WHERE status='running'",(now(),))

    def reconcile(self, aid, submitted: bool, note: str):
        if not isinstance(note,str) or len(note.strip())<10:
            raise ValueError("Describe how you verified the outcome")
        with self.transaction():
            r=self.db.execute("SELECT state,job_id FROM applications WHERE id=?",(aid,)).fetchone()
            if not r or r[0] not in ("unknown","awaiting_verification"):
                raise ValueError("Only uncertain or verification-pending submissions can be reconciled")
            state="confirmed" if submitted else "not_submitted"
            self.db.execute("UPDATE applications SET state=?,confirmation=?,updated=? WHERE id=?",(state,note,now(),aid))
            self.db.execute("UPDATE jobs SET status=?,reason=?,updated=? WHERE id=?",(state,note,now(),r[1]))
            self.event("human_reconciliation",aid,{"submitted":submitted,"note":note})
        # A not-submitted outcome is deliberately not retried automatically.

    def retry_not_submitted(self, aid, note):
        if not isinstance(note,str) or len(note.strip())<10:raise ValueError('Explain why this application can be retried')
        with self.transaction():
            app=self.db.execute('SELECT * FROM applications WHERE id=?',(aid,)).fetchone()
            if not app or app['state']!='not_submitted':raise ValueError('Reconcile as not submitted before retrying')
            self.check_job_decision(app['job_id'])
            # Preserve the entire prior attempt before freeing the unique job slot.
            self.event('application_retry_requested',aid,{'previous_application':dict(app),'note':note})
            self.db.execute('DELETE FROM verification_challenges WHERE application_id=?',(aid,))
            self.db.execute('DELETE FROM applications WHERE id=?',(aid,))
            self.db.execute("UPDATE jobs SET status='discovered',reason='',updated=? WHERE id=?",(now(),app['job_id']))
            return app['job_id']

    def application_record(self, aid):
        if not isinstance(aid,str) or not aid or len(aid)>100:
            raise ValueError('Choose an existing application record')
        row=self.db.execute('SELECT * FROM applications WHERE id=?',(aid,)).fetchone()
        return dict(row) if row else None

    def snapshot(self,material_offset=0,include_packages=True,question_limit=None,*,material_search='',material_status='all'):
        from .materials import basic_context
        if type(material_offset) is not int or not 0<=material_offset<=1000000:raise ValueError("Invalid material page")
        if question_limit is not None and (type(question_limit) is not int or not 1<=question_limit<=100):raise ValueError('Invalid question limit')
        from .material_ledger import search_materials
        material_page=search_materials(self,search=material_search,status=material_status,offset=material_offset)
        def rows(q,parameters=()): return [dict(x) for x in self.db.execute(q,parameters)]
        from .presentation import attention_sql,actionable_question_sql
        condition,parameters=attention_sql('j')
        question_condition,question_parameters=actionable_question_sql('q')
        priority=f"({condition} OR EXISTS(SELECT 1 FROM questions q WHERE q.job_id=j.id AND {question_condition}))"
        from .saved_views import list_views
        from .context_inbox import open_needs
        return {"basic_context":basic_context(self),"context_needs":open_needs(self),"saved_views":list_views(self),"settings":self.settings(),"templates":self.templates(),"facts":self.facts(False),"missing_setup":self.missing_setup(),
                "jobs":rows(f"SELECT j.* FROM jobs j ORDER BY {priority} DESC,j.score DESC,j.first_seen DESC LIMIT 500",(*parameters,*question_parameters)),
                "applications":rows(f"SELECT {'*' if include_packages else APPLICATION_METADATA} FROM applications ORDER BY (state IN ('unknown','awaiting_verification')) DESC,created DESC LIMIT 500"),
                "questions":rows("SELECT q.* FROM questions q WHERE "+question_condition+" ORDER BY q.rowid" + (" LIMIT ?" if question_limit is not None else ''), (*question_parameters,question_limit) if question_limit is not None else question_parameters),
                "runs":rows("SELECT * FROM runs ORDER BY started DESC LIMIT 30"),
                "sources":rows("SELECT * FROM sources ORDER BY (error!='') DESC,checked DESC,id LIMIT 100"),
                "employer_accounts":rows("SELECT * FROM employer_accounts ORDER BY (state IN ('uncertain','creating','signing_in')) DESC,updated DESC,id LIMIT 100"),
                "documents":[{**row,'available':self.document_available(row['kind'])} for row in rows("SELECT * FROM documents")],"materials":material_page["materials"],"material_count":material_page["total"],"material_offset":material_page["offset"],"material_total":material_page["library_total"],"material_search":material_page["search"],"material_status":material_page["status"]}


@contextlib.contextmanager
def worker_lock(root: Path, name="worker"):
    private_dir(root)
    path=root / (name + ".lock")
    fd=os.open(path,os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    try:
        try: fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError: raise Blocked("worker_busy")
        yield
    finally:
        os.close(fd)
