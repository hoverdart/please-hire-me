"""Failure evidence for explicitly selected synthetic dashboard browser tests."""
from contextlib import contextmanager
import json
from pathlib import Path
import re
import time
from urllib.parse import urlsplit


def open_fixture_dashboard(page, base):
    """Wait for initial data before starting the existing render deadlines."""
    with page.expect_response(lambda response: response.request.method == 'GET'
            and response.url.split('?', 1)[0] == base + '/api/state', timeout=30000) as initial:
        page.goto(base + '/#token=fixture-capability')
    assert initial.value.status == 200


@contextmanager
def dashboard_evidence(label):
    """Capture before Playwright closes; retain the original failure and timeout."""
    assert re.fullmatch(r'[a-z-]+', label)
    pages = []

    def observe(page):
        started = time.monotonic()
        evidence = {'api_responses': [], 'request_failures': [], 'page_errors': []}
        pages.append((page, evidence))

        def response(value):
            path = urlsplit(value.url).path
            if path.startswith('/api/'):
                evidence['api_responses'].append({'path': path, 'status': value.status,
                    'elapsed_ms': round((time.monotonic() - started) * 1000)})
                del evidence['api_responses'][:-100]

        def failed(request):
            evidence['request_failures'].append({'path': urlsplit(request.url).path,
                'reason': request.failure, 'elapsed_ms': round((time.monotonic() - started) * 1000)})
            del evidence['request_failures'][:-20]

        def page_error(error):
            evidence['page_errors'].append(str(error)[:1000])
            del evidence['page_errors'][:-20]

        page.on('response', response)
        page.on('requestfailed', failed)
        page.on('pageerror', page_error)
        page.add_init_script("""(() => {
            window.__dashboardDialogEvents = [];
            for (const method of ['show', 'showModal', 'close']) {
                const original = HTMLDialogElement.prototype[method];
                HTMLDialogElement.prototype[method] = function (...args) {
                    window.__dashboardDialogEvents.push({method, id: this.id,
                        elapsed_ms: performance.now(), stack: new Error().stack?.slice(0, 1000)});
                    window.__dashboardDialogEvents = window.__dashboardDialogEvents.slice(-20);
                    return original.apply(this, args);
                };
            }
        })();""")

    try:
        yield observe
    except BaseException as failure:
        directory = Path('test-results/dashboard')
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        for index, (page, evidence) in enumerate(pages):
            evidence['failure_type'] = type(failure).__name__
            try:
                evidence['ui'] = page.evaluate("""() => {
                    const dialog = document.querySelector('#job-dialog');
                    const ancestors = [];
                    for (let node = dialog; node; node = node.parentElement) {
                        const style = getComputedStyle(node);
                        ancestors.push({tag: node.tagName, id: node.id, hidden: node.hidden,
                            display: style.display, visibility: style.visibility});
                    }
                    return {ready_state: document.readyState, visibility: document.visibilityState,
                        state_loaded: typeof state !== 'undefined' && state !== null,
                        view: typeof view === 'undefined' ? null : view,
                        connection_visible: !document.querySelector('#connection-status')?.hidden,
                        run_count: document.querySelectorAll('#runs .run-row').length,
                        job_count: document.querySelectorAll('#jobs .opportunity-details').length,
                        dialog_open: dialog?.open, dialog_ancestors: ancestors,
                        dialog_events: window.__dashboardDialogEvents || []};
                }""")
                page.screenshot(path=str(directory / f'{label}-{index}.png'), full_page=True, timeout=5000)
            except Exception as error:
                evidence['capture_error_type'] = type(error).__name__
            (directory / f'{label}-{index}.json').write_text(json.dumps(evidence, indent=2))
            print('DASHBOARD_FAILURE_EVIDENCE', json.dumps(evidence, sort_keys=True))
        raise
