"""Smoke-test built wheels outside the checkout using disposable applicant storage."""
import argparse
import os
import subprocess
import tempfile
import venv
from pathlib import Path


SMOKE_TEST = """from pathlib import Path
import hireme,json,socket,subprocess,sys,time,urllib.error,urllib.request
assert Path(hireme.__file__).resolve().is_relative_to(Path(sys.prefix).resolve())
from hireme.discovery import board_sources
from hireme.store import Store
from importlib.resources import files
assert board_sources(Path.cwd(),limit=2)
for name in ('index.html','app.js','notes.js','workspace.js','recruiting.js','insights.js','history-import.js','style.css'):
    assert files('hireme').joinpath('static',name).is_file(), name
from hireme.platform_connections import list_connections
from hireme.application_artifacts import library
from hireme.backup import create_backup,restore_backup
from hireme.materials import import_material,review_material,writing_context
from hireme.connections import save_key,status
from hireme.onboarding import import_resume
from reportlab.pdfgen.canvas import Canvas
s=Store(Path.home()/'private')
try:
    assert not s.settings()['live_enabled']
    assert s.settings()['provider_model']=='sonnet'
    assert not s.facts()
    assert s.missing_setup()
    assert all(not c['enabled'] and not c['discovery_enabled'] and not c['native_apply_enabled'] for c in list_connections(s))
    assert library(s)['total']==0
    resume=Path.home()/'synthetic-resume.pdf'
    canvas=Canvas(str(resume));canvas.drawString(72,720,'Release Fixture');canvas.save()
    import_resume(s,resume)
    s.put_facts({'full_name':'Release Fixture','email':'fixture@candidate.invalid'})
    text='I built and tested a synthetic local application for this release check.'
    material=import_material(s,text.encode(),'fixture-context.txt','context')
    assert not material['confirmed'] and not s.templates()
    review_material(s,material['id'],text,'personal',True)
    assert s.templates()
    context=writing_context(s)
    # Configure a synthetic connection without sending an inference request.
    s.update_settings({'provider':'openai-api','provider_model':'release-fixture'})
    save_key(s,'openai-api','synthetic-release-fixture-key')
    assert status(s)['ready']
    create_backup(s,Path.home()/'history.zip')
finally:
    s.close()
restore_backup(Path.home()/'history.zip',Path.home()/'restored')
s=Store(Path.home()/'restored')
try:
    assert not s.settings()['live_enabled']
    assert s.facts()['email']['value']=='fixture@candidate.invalid'
    assert s.document_available('resume') and s.templates()
    assert writing_context(s)==context
    assert not status(s)['ready']  # Credentials require a separate reconnection.
    assert s.db.execute('SELECT COUNT(*) FROM model_requests').fetchone()[0]==0
    assert s.db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
finally:
    s.close()
with socket.socket() as listener:
    listener.bind(('127.0.0.1',0));port=listener.getsockname()[1]
process=subprocess.Popen([sys.executable,'-I','-m','hireme','--data-dir',str(Path.home()/'restored'),
                          'dashboard','--port',str(port),'--hide-token'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
try:
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    origin='http://127.0.0.1:'+str(port)
    deadline=time.monotonic()+15
    while True:
        assert process.poll() is None, 'Installed dashboard stopped during startup'
        try:
            with opener.open(origin+'/',timeout=1) as response:
                assert b'HireMeMaybe' in response.read()
            break
        except (OSError,urllib.error.URLError):
            if time.monotonic()>=deadline: raise AssertionError('Installed dashboard did not start') from None
            time.sleep(.1)
    try:
        opener.open(origin+'/api/state',timeout=5)
        raise AssertionError('Installed dashboard exposed unauthenticated state')
    except urllib.error.HTTPError as error:
        assert error.code==403
    token=json.loads((Path.home()/'restored/config/dashboard-token.json').read_text())['token']
    request=urllib.request.Request(origin+'/api/state',headers={'X-Hireme-Token':token})
    with opener.open(request,timeout=15) as response: snapshot=json.load(response)
    assert snapshot['settings']['live_enabled'] is False
    assert snapshot['facts']['email']['value']=='fixture@candidate.invalid'
    assert 'synthetic-release-fixture-key' not in json.dumps(snapshot)
finally:
    process.terminate()
    try: process.wait(timeout=10)
    except subprocess.TimeoutExpired: process.kill();process.wait(timeout=5)
print('Installed artifact: resources, private onboarding, approved context, connection isolation, restoration and authenticated dashboard passed')
"""


def check_wheel(wheel):
    wheel=Path(wheel)
    if wheel.is_symlink() or not wheel.is_file() or wheel.suffix!='.whl':
        raise ValueError('Choose a regular built wheel')
    wheel=wheel.resolve()
    with tempfile.TemporaryDirectory(prefix='hireme-install-') as raw:
        root=Path(raw);environment=root/'venv'
        venv.EnvBuilder(with_pip=True,system_site_packages=False).create(environment)
        python=environment/'bin/python'
        subprocess.run([str(python),'-I','-m','pip','install',str(wheel)],check=True,cwd=root)
        subprocess.run([str(python),'-I','-m','pip','check'],check=True,cwd=root)
        home=root/'home';home.mkdir()
        env={k:v for k,v in os.environ.items() if k in {'PATH','LANG','LC_ALL','SYSTEMROOT'}}
        env.update(HOME=str(home),USER='release-fixture',PYTHONNOUSERSITE='1')
        subprocess.run([str(python),'-I','-m','hireme','--help'],check=True,cwd=root,env=env,stdout=subprocess.DEVNULL)
        subprocess.run([str(python),'-I','-c',SMOKE_TEST],check=True,cwd=root,env=env)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('wheels',nargs='+',type=Path)
    args=parser.parse_args(argv)
    for wheel in args.wheels:
        try:check_wheel(wheel)
        except ValueError as error:parser.exit(1,str(error)+'\n')
    return 0


if __name__=='__main__':
    raise SystemExit(main())
