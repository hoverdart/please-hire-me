"""Terminal commands bound to the workspace currently open in the portal."""
import shlex
import sys


def command(store, *arguments):
    return shlex.join([sys.executable, '-I', '-m', 'hireme', '--data-dir',
                       str(store.root), *arguments])
