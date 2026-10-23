"""Release locks agree with each other and the installable package contract."""
from pathlib import Path
import tomllib

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from packaging.version import Version


ROOT = Path(__file__).resolve().parents[1]


def locked_versions(name):
    versions = {}
    for line in (ROOT / 'requirements' / name).read_text().splitlines():
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        requirement = Requirement(line)
        key = canonicalize_name(requirement.name)
        pins = list(requirement.specifier)
        assert not requirement.url and not requirement.marker and not requirement.extras
        assert len(pins) == 1 and pins[0].operator == '==' and '*' not in pins[0].version
        assert key not in versions, f'Duplicate dependency: {key}'
        versions[key] = Version(pins[0].version)
    return versions


def test_development_lock_preserves_every_runtime_pin():
    runtime = locked_versions('runtime.lock')
    development = locked_versions('development.lock')
    assert runtime and all(development.get(key) == version for key, version in runtime.items())


def test_locks_satisfy_declared_core_and_optional_dependencies():
    project = tomllib.loads((ROOT / 'pyproject.toml').read_text())['project']
    runtime = locked_versions('runtime.lock')
    development = locked_versions('development.lock')
    for group, declarations in [('core', project['dependencies']), *project['optional-dependencies'].items()]:
        versions = development if group == 'test' else runtime
        for declaration in declarations:
            requirement = Requirement(declaration)
            version = versions.get(canonicalize_name(requirement.name))
            assert version is not None and version in requirement.specifier, declaration
