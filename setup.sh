#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
install_only=false
if [[ "${1:-}" == "--install-only" && $# -eq 1 ]]; then
  install_only=true
elif [[ $# -ne 0 ]]; then
  echo 'Usage: ./setup.sh [--install-only]' >&2
  exit 2
fi
command -v python3 >/dev/null || { echo 'Python 3.11+ is required.' >&2; exit 1; }
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else "Python 3.11+ is required.")'
if [[ "${HIREME_PI:-0}" == "1" ]]; then
  python3 -c 'import sys; sys.exit(0 if sys.platform == "linux" and sys.maxsize > 2**32 else "Pi setup requires 64-bit Linux and a 64-bit Python.")'
  if ! command -v chromium >/dev/null && ! command -v chromium-browser >/dev/null; then
    echo 'Install system Chromium first with sudo apt install chromium.' >&2
    exit 1
  fi
fi
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements/runtime.lock
.venv/bin/python -m pip install --no-deps -e .
.venv/bin/python -m pip check
if [[ "${HIREME_PI:-0}" != "1" ]]; then
  .venv/bin/python -m playwright install chromium
elif [[ "$install_only" == false ]]; then
  .venv/bin/python -m hireme pi configure
fi
if [[ "$install_only" == true ]]; then
  exit 0
fi
printf '\nOpen the printed dashboard URL. Open Setup, import your resume and confirm your facts. Finish paused and review prepared drafts before enabling submissions.\n'
exec .venv/bin/python -m hireme dashboard --open
