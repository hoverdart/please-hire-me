"""Discovery resources available in source checkouts and installed wheels."""
from pathlib import Path
from importlib.resources import files

def discovery_text(repo,name):
    if name not in {'boards.md','slug-candidates.txt'}:raise ValueError('Unknown discovery resource')
    path=Path(repo)/'data'/name
    return path.read_text() if path.is_file() else files('hireme').joinpath('resources',name).read_text()
