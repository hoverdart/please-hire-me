from __future__ import annotations

import os
import platform
import shutil
import sys
import threading
import time
from pathlib import Path

from .connections import status

_BROWSER_CACHE = {}
_BROWSER_CACHE_LOCK = threading.Lock()
_BROWSER_CACHE_SECONDS = 60


def _detect_browser(channel):
    if channel == 'system-chromium':
        return bool(shutil.which('chromium') or shutil.which('chromium-browser'))
    if channel == 'chrome':
        return bool(shutil.which('google-chrome') or shutil.which('chrome')
                    or Path('/Applications/Google Chrome.app').exists())
    from playwright.sync_api import sync_playwright
    with sync_playwright() as playwright:
        return Path(playwright.chromium.executable_path).is_file()


def browser_available(channel, force=False):
    # Polling the dashboard should not spawn a Playwright driver every 15 seconds.
    # A start/setup verification always bypasses the cache. The actual worker still
    # opens the configured browser and reports any runtime failure itself.
    key = (channel, os.environ.get('PATH'), os.environ.get('PLAYWRIGHT_BROWSERS_PATH'))
    with _BROWSER_CACHE_LOCK:
        cached = _BROWSER_CACHE.get(key)
        if not force and cached and time.monotonic() - cached[0] < _BROWSER_CACHE_SECONDS:
            return cached[1]
        result = _detect_browser(channel)
        _BROWSER_CACHE[key] = (time.monotonic(), result)
        return result


def readiness(store, verify=False):
    settings = store.settings()
    return {
        'platform': sys.platform,
        'architecture': platform.machine(),
        'python': platform.python_version(),
        'browser_ready': browser_available(settings['browser_channel'], force=verify),
        'provider': status(store, verify),
        'missing': store.missing_setup(),
        'deployment': settings['deployment'],
        'supported_platform': sys.platform in ('darwin', 'linux'),
    }
