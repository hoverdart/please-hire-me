"""Actionable, read-only local setup diagnostics. No inference or submissions."""
from __future__ import annotations

from .config import FACTS
from .setup_status import readiness
from .recovery import worker_status


def diagnose(store, verify=False, *, checks=None):
    if checks is None: checks = readiness(store, verify=verify)
    items = []
    items.append({'name': 'Operating system', 'ready': checks['supported_platform'],
                  'detail': f"{checks['platform']} · {checks['architecture']} · Python {checks['python']}",
                  'action': '' if checks['supported_platform'] else 'Use macOS or Linux. On Windows, use WSL2.'})
    channel = store.settings()['browser_channel']
    command = 'Install system Chromium.' if channel == 'system-chromium' else 'Install Google Chrome.' if channel == 'chrome' else 'Run: python -m playwright install chromium'
    items.append({'name': 'Application browser', 'ready': checks['browser_ready'],
                  'detail': channel, 'action': '' if checks['browser_ready'] else command})
    provider = checks['provider']
    # Installed does not mean authenticated; a normal doctor checks installation only.
    items.append({'name': 'Model connection', 'ready': provider['ready'], 'detail': provider['message'],
                  'action': '' if provider['ready'] else 'Open Model connection in the dashboard to configure your provider.'})
    missing = checks['missing']
    items.append({'name': 'Applicant setup', 'ready': not missing,
                  'detail': 'Required documents and facts are saved.' if not missing else 'Still needed: ' + ', '.join(FACTS.get(key, key) for key in missing),
                  'action': '' if not missing else 'Open the dashboard and follow Setup checklist.'})
    settings = store.settings()
    worker = worker_status(store)
    items.append({'name': 'Worker history', 'ready': not worker['recovery_needed'],
                  'detail': 'A batch is running.' if worker['running'] else 'Interrupted work needs recovery.' if worker['recovery_needed'] else 'No interrupted work.',
                  'action': 'Run: hireme recover. Applications stay paused while uncertain outcomes await verification.' if worker['recovery_needed'] else ''})
    items.append({'name': 'Submission control', 'ready': True,
                  'detail': 'Automatic submissions enabled.' if settings['live_enabled'] else 'Submissions paused.',
                  'action': ''})
    return {'ready': all(item['ready'] for item in items), 'verified_login': verify,
            'checks': items, 'note': 'No model requests or applications were sent. API connectivity is checked on the first model request.'}


def format_report(result):
    lines = ['Application desk · setup check', '']
    for item in result['checks']:
        lines.append(f"{'OK' if item['ready'] else 'NEEDS SETUP'}  {item['name']}: {item['detail']}")
        if item['action']: lines.append('  Next: ' + item['action'])
    lines.extend(['', result['note']])
    if not result['verified_login']: lines.append('To check subscription CLI login too, run: hireme doctor --verify-login')
    return '\n'.join(lines)


def dashboard_diagnostics(store, *, demo=False):
    """A dated, read-only report containing fixed setup details, never applicant values."""
    from .util import now
    if demo:
        from .demo import DEMO_READINESS
        checks = {**DEMO_READINESS, 'missing': store.missing_setup()}
    else:
        from .setup_status import browser_available
        browser_available(store.settings()['browser_channel'], force=True)
        checks = readiness(store)
    result = diagnose(store, checks=checks)
    result['checked_at'] = now()
    result['demo'] = demo
    result['report'] = format_report(result) + '\nChecked at: ' + result['checked_at'] + '\n'
    return result
