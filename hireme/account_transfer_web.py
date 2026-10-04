"""Bounded dashboard transport for explicit encrypted employer-password transfers."""
from __future__ import annotations

import base64
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory

from .account_transfer import MAX_ARCHIVE, export_accounts, import_accounts
from .util import Blocked, write_private_blob


@contextmanager
def _transfer_errors():
    try: yield
    except Blocked as error:
        messages={'worker_busy':'Wait for active work to finish before transferring employer passwords.',
                  'account_credentials_unavailable':'Stored employer passwords are unavailable. Import the matching encrypted transfer from your original computer after restoring its history.',
                  'account_identity_unconfirmed':'Confirm your applicant email before transferring employer passwords.'}
        raise ValueError(messages.get(error.reason,'The employer-password transfer could not finish. Review account history and try again.')) from None
    except OSError:
        raise ValueError('The local credential transfer could not finish. Check private storage and try again.') from None


def _passphrase(value):
    if not isinstance(value,str) or not 12<=len(value)<=1024:
        raise ValueError('Use a transfer passphrase of 12–1024 characters')
    try: value.encode('utf-8')
    except UnicodeError: raise ValueError('Use readable Unicode characters in the transfer passphrase') from None


def export_payload(store,passphrase,confirmation):
    _passphrase(passphrase)
    if confirmation!=passphrase: raise ValueError('The transfer passphrases must match')
    with _transfer_errors(), TemporaryDirectory(dir=store.root,prefix='.account-transfer-') as directory:
        destination=Path(directory)/'accounts.encrypted'
        export_accounts(store,destination,passphrase)
        return destination.read_bytes()


def import_payload(store,encoded,passphrase):
    _passphrase(passphrase)
    maximum=((MAX_ARCHIVE+2)//3)*4
    if not isinstance(encoded,str) or not encoded or len(encoded)>maximum:
        raise ValueError('Choose an encrypted credential transfer up to 2 MiB')
    try: data=base64.b64decode(encoded,validate=True)
    except (ValueError,TypeError): raise ValueError('Choose a valid encrypted credential transfer') from None
    if not 0<len(data)<=MAX_ARCHIVE:
        raise ValueError('Choose an encrypted credential transfer up to 2 MiB')
    with _transfer_errors(), TemporaryDirectory(dir=store.root,prefix='.account-transfer-') as directory:
        source=Path(directory)/'accounts.encrypted'
        write_private_blob(source,data)
        return import_accounts(store,source,passphrase)
