"""Only the three commands granted to the restricted off-device backup SSH key."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shlex
import shutil
import sys
import tempfile

from .util import atomic_json, private_dir


def _worker_path():
    """Use the installed worker's CLI search path, not SSH's minimal PATH."""
    unit=Path.home()/'.config/systemd/user/please-hire-me-worker.service'
    if unit.is_file() and not unit.is_symlink() and unit.stat().st_size<=65536:
        for line in unit.read_text().splitlines():
            if not line.startswith('Environment='):continue
            for item in shlex.split(line.split('=',1)[1]):
                if item.startswith('PATH='):
                    return item[5:].replace('%%','%')
    return str(Path.home()/'.local/bin')+':'+os.environ.get('PATH','/usr/bin:/bin')


def stream_backup(store, output):
    from .backup import create_backup
    with tempfile.TemporaryDirectory(prefix='.backup-stream-',dir=private_dir(store.root/'tmp')) as directory:
        archive=Path(directory)/'history.zip'
        create_backup(store,archive)
        with archive.open('rb') as source:shutil.copyfileobj(source,output,64*1024)
        output.flush()


def execute(root, command, *, output=None, input_stream=None):
    if command=='health':
        os.environ['PATH']=_worker_path()
        from .health import check_health
        result=check_health(root)
        print(json.dumps(result))
        return 0  # Deliver warning/critical JSON to the monitor as evidence.
    if command=='backup':
        from .store import Store
        store=Store(root)
        try:stream_backup(store,output or sys.stdout.buffer)
        finally:store.close()
        return 0
    if command=='backup-ack':
        from .health import _age
        import time
        raw=(input_stream or sys.stdin).read(4097)
        if len(raw)>4096:raise ValueError('Invalid backup acknowledgement')
        data=json.loads(raw)
        if (not isinstance(data,dict) or set(data)!={'verified_at','sha256','bytes','applications'}
                or not isinstance(data['sha256'],str) or not re.fullmatch('[a-f0-9]{64}',data['sha256'])
                or type(data['bytes']) is not int or not 0<data['bytes']<=9*1024**3
                or type(data['applications']) is not int or data['applications']<0
                or _age(data['verified_at'],time.time()) is None
                or _age(data['verified_at'],time.time())>3600):
            raise ValueError('Invalid backup acknowledgement')
        atomic_json(Path(root)/'operations'/'backup-ack.json',data)
        print(json.dumps({'recorded':True}))
        return 0
    raise ValueError('This SSH key permits health, backup and backup-ack only')
