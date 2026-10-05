import sqlite3
from concurrent.futures import ThreadPoolExecutor
import pytest
from hireme.store import Store
from hireme.util import digest


def test_initialized_store_opens_without_waiting_for_active_writer(store):
    writer=sqlite3.connect(store.path,isolation_level=None)
    writer.execute('BEGIN IMMEDIATE')
    writer.execute("UPDATE facts SET value='Uncommitted change' WHERE key='full_name'")
    def read():
        observer=Store(store.root)
        try:return observer.facts()['full_name']['value']
        finally:observer.close()
    with ThreadPoolExecutor(max_workers=1) as pool:
        future=pool.submit(read)
        try:assert future.result(timeout=2)=='Test Person'
        finally:writer.rollback();writer.close()


def test_legacy_store_initialization_preserves_config_and_facts(store):
    before=store.facts();settings=store.settings()
    store.db.execute('DROP TABLE schema_metadata')
    observer=Store(store.root)
    try:
        assert observer.facts()==before and observer.settings()==settings
        assert observer.db.execute('SELECT fingerprint FROM schema_metadata').fetchone()[0]
    finally:observer.close()


def test_changed_schema_applies_once_and_preserves_existing_data(store,monkeypatch):
    from hireme import store as module
    updated=module.SCHEMA+'\nCREATE TABLE IF NOT EXISTS fixture_migration (id INTEGER PRIMARY KEY);'
    monkeypatch.setattr(module,'SCHEMA',updated)
    observer=Store(store.root)
    try:
        assert observer.db.execute('SELECT fingerprint FROM schema_metadata').fetchone()[0]==digest(updated)
        assert observer.facts()==store.facts()
        assert observer.db.execute('SELECT count(*) FROM fixture_migration').fetchone()[0]==0
    finally:observer.close()


def test_failed_schema_change_does_not_record_readiness(store,monkeypatch):
    from hireme import store as module
    original=store.db.execute('SELECT fingerprint FROM schema_metadata').fetchone()[0]
    monkeypatch.setattr(module,'SCHEMA',module.SCHEMA+'\nINVALID SQL;')
    with pytest.raises(sqlite3.OperationalError):Store(store.root)
    assert store.db.execute('SELECT fingerprint FROM schema_metadata').fetchone()[0]==original
    assert store.facts()['full_name']['value']=='Test Person'


def test_missing_config_retains_conservative_initialization(store):
    store.db.execute('DELETE FROM config')
    observer=Store(store.root)
    try:
        assert not observer.settings()['live_enabled']
        assert not observer.settings()['onboarding_complete']
        assert observer.facts()['full_name']['value']=='Test Person'
    finally:observer.close()


def test_external_schema_change_repairs_missing_table_without_resetting_facts(store):
    before=store.facts();store.db.execute('DROP TABLE job_notes')
    observer=Store(store.root)
    try:
        assert observer.db.execute('SELECT count(*) FROM job_notes').fetchone()[0]==0
        assert observer.facts()==before
        metadata=observer.db.execute('SELECT schema_version FROM schema_metadata').fetchone()[0]
        assert metadata==observer.db.execute('PRAGMA schema_version').fetchone()[0]
    finally:observer.close()


def test_previous_fingerprint_marker_upgrades_without_resetting_config(store):
    before=store.settings();store.db.execute('DROP TABLE schema_metadata')
    store.db.execute('CREATE TABLE schema_metadata (id INTEGER PRIMARY KEY,fingerprint TEXT NOT NULL)')
    store.db.execute("INSERT INTO schema_metadata VALUES(1,'previous-version')")
    observer=Store(store.root)
    try:
        assert observer.settings()==before
        assert observer.db.execute('SELECT schema_version FROM schema_metadata').fetchone()[0]>0
    finally:observer.close()
