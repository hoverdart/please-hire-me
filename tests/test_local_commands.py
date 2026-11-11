import shlex
from pathlib import Path
from types import SimpleNamespace

from hireme.local_commands import command


def test_workspace_command_quotes_paths_without_shell_interpretation(monkeypatch):
    monkeypatch.setattr('hireme.local_commands.sys.executable', '/Some Folder/python')
    store = SimpleNamespace(root=Path('/private/applicant $(touch unexpected)'))
    assert shlex.split(command(store, 'gmail', 'connect')) == [
        '/Some Folder/python', '-I', '-m', 'hireme', '--data-dir',
        '/private/applicant $(touch unexpected)', 'gmail', 'connect']
