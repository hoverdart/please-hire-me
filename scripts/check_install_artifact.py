"""Smoke-test a built wheel outside the checkout, without credentials or network."""
import os,subprocess,sys,tempfile,venv
from pathlib import Path
wheel=Path(sys.argv[1]).resolve()
with tempfile.TemporaryDirectory(prefix='hireme-install-') as raw:
    root=Path(raw);environment=root/'venv';venv.EnvBuilder(with_pip=True,system_site_packages=True).create(environment)
    python=environment/'bin/python'
    subprocess.run([str(python),'-m','pip','install','--no-deps',str(wheel)],check=True,cwd=root)
    home=root/'home';home.mkdir()
    env={k:v for k,v in os.environ.items() if k in {'PATH','LANG','LC_ALL','SYSTEMROOT'}}
    env.update(HOME=str(home),USER='release-fixture',PYTHONNOUSERSITE='1')
    code="""from pathlib import Path
import hireme,sys
assert Path(hireme.__file__).resolve().is_relative_to(Path(sys.prefix).resolve())
from hireme.discovery import board_sources
from hireme.store import Store
from importlib.resources import files
assert board_sources(Path.cwd(),limit=2)
assert files('hireme').joinpath('static','index.html').is_file()
s=Store(Path.home()/'private')
assert not s.settings()['live_enabled']
assert s.settings()['provider_model']=='sonnet'
assert not s.facts()
assert s.missing_setup()
s.close()
print('Installed artifact: discovery, dashboard assets, private onboarding and conservative defaults passed')
"""
    subprocess.run([str(python),'-c',code],check=True,cwd=root,env=env)
