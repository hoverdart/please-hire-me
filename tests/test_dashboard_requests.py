"""Reject incomplete or ambiguous local writes before opening applicant storage."""
import json
import multiprocessing
import socket
from pathlib import Path

import pytest

from tests.test_dashboard import launch, wait_for_dashboard


def launch_requests(root,repo,port):
    from hireme import server
    server.REQUEST_TIMEOUT=0.5
    launch(root,repo,port)


@pytest.fixture
def dashboard(tmp_path):
    root = tmp_path / 'private'
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0)); port = listener.getsockname()[1]
    process = multiprocessing.Process(target=launch_requests, args=(str(root), str(Path.cwd()), port))
    process.start()
    try:
        wait_for_dashboard(process, f'http://127.0.0.1:{port}')
        yield root, port
    finally:
        process.terminate(); process.join(5)


def request(port, headers, body=b'', token='fixture-capability',path='/api/facts',response_timeout=5):
    with socket.create_connection(('127.0.0.1', port), timeout=5) as connection:
        lines = [f'POST {path} HTTP/1.1', f'Host: 127.0.0.1:{port}',
                 f'X-Hireme-Token: {token}', *headers, 'Connection: close', '', '']
        connection.sendall('\r\n'.join(lines).encode('latin-1') + body)
        connection.shutdown(socket.SHUT_WR)
        connection.settimeout(response_timeout)
        chunks = []
        while chunk := connection.recv(65536):
            chunks.append(chunk)
    response = b''.join(chunks)
    status = int(response.split(b' ', 2)[1])
    return status, json.loads(response.split(b'\r\n\r\n', 1)[1])


def test_truncated_json_cannot_apply_a_valid_prefix(dashboard):
    root, port = dashboard
    body = b'{"facts":{"full_name":"Synthetic applicant"}}'
    status, result = request(port, [f'Content-Length: {len(body) + 10}'], body)
    assert status == 400 and 'interrupted' in result['error']
    assert not root.exists()


@pytest.mark.parametrize('path',['/api/resume','/api/gmail-client','/api/material-upload'])
def test_truncated_upload_cannot_import_documents_or_credentials(dashboard,path):
    root,port=dashboard
    status,result=request(port,['Content-Length: 200'],b'Synthetic incomplete upload',path=path)
    assert status==400 and 'interrupted' in result['error'] and not root.exists()


def test_stalled_body_times_out_without_initializing_storage(dashboard):
    root,port=dashboard
    with socket.create_connection(('127.0.0.1',port),timeout=5) as connection:
        connection.sendall((f'POST /api/facts HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n'
                            'X-Hireme-Token: fixture-capability\r\nContent-Length: 200\r\n\r\n{').encode())
        response=connection.recv(65536)
    assert response.startswith(b'HTTP/1.0 408') and not root.exists()


@pytest.mark.parametrize('headers', [
    ['Content-Length: 2', 'Content-Length: 2'],
    ['Content-Length: 2', 'Content-Length: 200'],
    ['Content-Length: +2'], ['Content-Length: -1'], ['Content-Length: 2_0'],
    ['Content-Length: 2', 'Transfer-Encoding: chunked'],
    ['Transfer-Encoding: chunked'],
])
def test_ambiguous_request_framing_never_mutates_state(dashboard, headers):
    root, port = dashboard
    status, _ = request(port, headers, b'{}')
    assert status == 400 and not root.exists()


@pytest.mark.parametrize('body', [b'null', b'[]', b'"value"', b'1', b'{} {}', b'\xff'])
def test_invalid_json_action_leaves_fresh_storage_uninitialized(dashboard, body):
    root, port = dashboard
    status, _ = request(port, [f'Content-Length: {len(body)}'], body)
    assert status == 400 and not root.exists()


def test_non_ascii_capability_is_rejected_with_an_http_response(dashboard):
    root, port = dashboard
    status, _ = request(port, ['Content-Length: 2'], b'{}', token='caf\u00e9')
    assert status == 403 and not root.exists()


@pytest.mark.parametrize('header',['Host: attacker.invalid','X-Hireme-Token: duplicate','Origin: http://attacker.invalid'])
def test_duplicate_authority_headers_are_rejected(dashboard,header):
    root,port=dashboard
    headers=['Content-Length: 2',header]
    if header.startswith('Origin:'):headers.append(f'Origin: http://127.0.0.1:{port}')
    status,_=request(port,headers,b'{}')
    assert status==403 and not root.exists()


def test_complete_json_write_still_saves_confirmed_facts(dashboard):
    root, port = dashboard
    body = b'{"facts":{"full_name":"Synthetic applicant"}}'
    # First-run schema creation must complete durable SD-card writes. Keep the
    # short rejection/stalled-body budgets separate from this valid bootstrap.
    status, _ = request(port, [f'Content-Length: {len(body)}'], body,response_timeout=30)
    assert status == 200
    from hireme.store import Store
    with_store = Store(root)
    try:
        assert with_store.facts()['full_name']['value'] == 'Synthetic applicant'
    finally:
        with_store.close()
