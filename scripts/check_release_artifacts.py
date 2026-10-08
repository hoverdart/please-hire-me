"""Reject private file classes in distribution archives without printing contents."""
import re,sys,tarfile,zipfile
from pathlib import PurePosixPath

def validate(names):
    for name in names:
        path=PurePosixPath(name)
        if path.is_absolute() or '..' in path.parts:raise ValueError('Unsafe artifact member path')
        if re.search(r'(?:^|/)(?:\.env(?:\..*)?|ledger\.sqlite3(?:-.*)?|credentials\.json|token\.json|profile\.json|answers\.md|client_secret_[^/]+|[^/]+\.(?:pem|key))$',name,re.I):raise ValueError('Private file class in release artifact: '+name)
        if any(p in {'applications','screenshots','browser','platform-browser','logs','state'} for p in path.parts):raise ValueError('Private storage directory in release artifact: '+name)

for archive in sys.argv[1:]:
    if archive.endswith('.whl'):
        with zipfile.ZipFile(archive) as z:validate(z.namelist())
    else:
        with tarfile.open(archive) as t:
            validate(m.name for m in t.getmembers())
            if any(m.issym() or m.islnk() for m in t.getmembers()):raise ValueError('Distribution archive contains links')
    print('Artifact private-file/path audit passed:',PurePosixPath(archive).name)
