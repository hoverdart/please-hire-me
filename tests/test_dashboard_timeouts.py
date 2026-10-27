from tests.dashboard_evidence import open_view
"""Stalled dashboard reads recover without discarding edits or retrying writes."""
import multiprocessing
import socket
from pathlib import Path

import pytest
from tests.test_dashboard import launch, wait_for_dashboard


@pytest.mark.parametrize('width', [320, 1440])
def test_stalled_read_releases_refresh_and_preserves_draft(tmp_path, width):
    from playwright.sync_api import sync_playwright, expect
    sock = socket.socket(); sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]; sock.close()
    process = multiprocessing.Process(target=launch, args=(str(tmp_path/'private'), str(Path.cwd()), port))
    process.start(); base = f'http://127.0.0.1:{port}'
    try:
        wait_for_dashboard(process, base)
        with sync_playwright() as p:
            browser = p.chromium.launch(); page = browser.new_page(viewport={'width': width, 'height': 900})
            page.clock.install()
            page.goto(base + '/#token=fixture-capability')
            expect(page.locator('#worker-state')).to_have_text('Setup needed')
            open_view(page, 'profile')
            draft = page.locator('#facts-form [name=full_name]')
            draft.fill('Unsaved applicant draft'); draft.focus()
            page.evaluate('''() => {
                window.originalFetch = window.fetch;
                window.fetch = (path, options) => {
                    if (String(path).startsWith('/api/state')) {
                        return new Promise((resolve, reject) => options.signal?.addEventListener(
                            'abort', () => reject(new DOMException('Aborted', 'AbortError')), {once: true}));
                    }
                    return originalFetch(path, options);
                };
                window.refreshDone = false;
                refresh().then(() => window.refreshDone = true);
            }''')
            page.clock.fast_forward(30001)
            expect(page.locator('#connection-status')).to_be_visible()
            expect(page.locator('#connection-message')).to_contain_text('too long to respond')
            page.wait_for_function('window.refreshDone')
            expect(draft).to_have_value('Unsaved applicant draft')
            assert page.evaluate('refreshing === null')
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            page.screenshot(path=f'/tmp/hireme-hardening-timeout-{width}.png', full_page=True)
            page.evaluate('window.fetch = window.originalFetch')
            page.locator('#retry-connection').click()
            expect(page.locator('#connection-status')).to_be_hidden()
            expect(draft).to_have_value('Unsaved applicant draft')
            # An in-flight save uses no read-abort deadline. This is an isolated
            # mock operation; no applicant data or real application is sent.
            page.evaluate('''() => {
                window.writeSignal = null;
                window.writeDone = false;
                window.fetch = (path, options) => String(path) !== '/api/synthetic-write'
                    ? originalFetch(path, options) : new Promise(resolve => {
                    window.writeSignal = options.signal;
                    window.completeWrite = () => resolve(new Response('{"ok":true}', {status:200}));
                });
                api('/api/synthetic-write', {value:'fixture'}).then(() => window.writeDone = true);
            }''')
            page.clock.fast_forward(30001)
            assert page.evaluate('!window.writeDone && window.writeSignal === undefined')
            page.evaluate('window.completeWrite()')
            page.wait_for_function('window.writeDone')
            browser.close()
    finally:
        process.terminate(); process.join(5)
