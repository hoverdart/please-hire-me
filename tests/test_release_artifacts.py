"""Public distributions must not publish runtime state or unsafe archive entries."""
import io
import stat
import tarfile
import zipfile

import pytest

from scripts.check_release_artifacts import audit_archive, main, validate


@pytest.mark.parametrize('name', [
    'release/config/dashboard-token.json', 'release/gmail-token.json',
    'release/gmail-client.json', 'release/gmail-account.json',
    'release/integrations/accounts/owner.json', 'release/config/settings.json',
    'release/config/profile.json', 'release/config/answers.md',
    'release/.env.production', 'release/client_secret.json', 'release/private.key',
    'release/ledger.sqlite3-wal', 'release/.git/config',
    'release/Browser/Default/Cookies', 'release/screenshots/private.jpg',
])
def test_private_runtime_file_classes_are_rejected_without_echoing_names(name):
    with pytest.raises(ValueError) as error:
        validate([name])
    assert name not in str(error.value)


@pytest.mark.parametrize('names', [
    [], ['../outside'], ['/absolute'], ['C:/outside'], ['release\\private'],
    ['release/unsafe\nname'], ['release/file', 'release/./file'],
])
def test_empty_unsafe_or_duplicate_archives_are_rejected(names):
    with pytest.raises(ValueError):
        validate(names)


def test_public_examples_and_dashboard_assets_remain_publishable():
    validate(['release/config/profile.example.json', 'release/config/settings.example.json',
              'release/config/answers.example.md', 'release/hireme/static/app.js',
              'release/docs/images/application-desk-demo.png'])


@pytest.mark.parametrize('kind', [stat.S_IFLNK, stat.S_IFIFO, stat.S_IFSOCK])
def test_wheel_links_and_special_files_are_rejected(tmp_path, kind):
    wheel = tmp_path / 'unsafe.whl'
    member = zipfile.ZipInfo('hireme/link')
    member.create_system = 3
    member.external_attr = (kind | 0o600) << 16
    with zipfile.ZipFile(wheel, 'w') as archive:
        archive.writestr(member, 'private target')
    with pytest.raises(ValueError, match='links or special'):
        audit_archive(wheel)


@pytest.mark.parametrize('kind', [tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.FIFOTYPE, tarfile.CHRTYPE])
def test_tar_links_and_special_files_are_rejected(tmp_path, kind):
    path = tmp_path / 'unsafe.tar.gz'
    with tarfile.open(path, 'w:gz') as archive:
        member = tarfile.TarInfo('release/link'); member.type = kind
        archive.addfile(member)
    with pytest.raises(ValueError, match='links or special'):
        audit_archive(path)


def test_cli_requires_archives_and_checks_every_argument(tmp_path, capsys):
    with pytest.raises(SystemExit) as error:
        main([])
    assert error.value.code == 2
    good = tmp_path / 'release.whl'
    with zipfile.ZipFile(good, 'w') as archive:
        archive.writestr('hireme/__init__.py', '')
    bad = tmp_path / 'private.tar.gz'
    with tarfile.open(bad, 'w:gz') as archive:
        data = b'synthetic secret'; member = tarfile.TarInfo('release/gmail-token.json')
        member.size = len(data); archive.addfile(member, io.BytesIO(data))
    with pytest.raises(SystemExit) as error:
        main([str(good), str(bad)])
    assert error.value.code == 1
    output = capsys.readouterr()
    assert 'audit passed' in output.out and 'audit failed' in output.err
    assert 'synthetic secret' not in output.err and 'gmail-token' not in output.err


def test_unreadable_archive_is_reported_without_a_traceback(tmp_path, capsys):
    path = tmp_path / 'broken.whl'; path.write_bytes(b'not a ZIP')
    with pytest.raises(SystemExit) as error:
        main([str(path)])
    assert error.value.code == 1
    assert 'Unreadable release archive' in capsys.readouterr().err
