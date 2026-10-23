"""Smoke-test built wheels outside the checkout using disposable applicant storage."""
import argparse
import os
import subprocess
import tempfile
import venv
from pathlib import Path


SMOKE_TEST = """from pathlib import Path
import hireme,sys
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
s=Store(Path.home()/'private')
try:
    assert not s.settings()['live_enabled']
    assert s.settings()['provider_model']=='sonnet'
    assert not s.facts()
    assert s.missing_setup()
    assert all(not c['enabled'] and not c['discovery_enabled'] and not c['native_apply_enabled'] for c in list_connections(s))
    assert library(s)['total']==0
    create_backup(s,Path.home()/'history.zip')
finally:
    s.close()
restore_backup(Path.home()/'history.zip',Path.home()/'restored')
s=Store(Path.home()/'restored')
try:
    assert not s.settings()['live_enabled'] and not s.facts()
    assert s.db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
finally:
    s.close()
print('Installed artifact: resources, private onboarding, conservative defaults and history restoration passed')
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
