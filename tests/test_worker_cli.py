import json

from hireme.cli import main


def test_scheduled_paused_run_exits_successfully_without_attempts(store, capsys):
    store.update_settings({'live_enabled': False})
    assert main(['--data-dir', str(store.root), 'run', '--no-discovery']) == 0
    assert 'paused' in capsys.readouterr().err
    run = store.db.execute('SELECT status,submitted,detail FROM runs').fetchone()
    assert run['status'] == 'paused' and run['submitted'] == 0
    assert json.loads(run['detail'])['attempts'] == 0
    assert not store.db.execute('SELECT 1 FROM applications').fetchone()
    assert not store.db.execute('SELECT 1 FROM model_requests').fetchone()


def test_paused_run_still_fails_for_incomplete_setup(store, capsys):
    store.update_settings({'live_enabled': False, 'onboarding_complete': False})
    assert main(['--data-dir', str(store.root), 'run', '--no-discovery']) == 2
    assert 'setup_incomplete' in capsys.readouterr().err
    assert store.db.execute('SELECT status FROM runs').fetchone()[0] == 'blocked'


def test_held_review_saves_progress_when_preparation_is_interrupted(store,monkeypatch,tmp_path):
    from hireme.util import Blocked
    report={'counts':{},'jobs':[],'forms_pending':2}
    monkeypatch.setattr('hireme.queue_review.revalidate',lambda *args,**kwargs:report)
    def prepare(*args):
        report.update(forms_pending=1,forms_prepared=1)
        raise Blocked('rollout_stale')
    monkeypatch.setattr('hireme.queue_review.prepare_review',prepare)
    output=tmp_path/'review.json'
    assert main(['--data-dir',str(store.root),'revalidate-holds','--prepare','--output',str(output)])==2
    assert json.loads(output.read_text())['forms_prepared']==1
