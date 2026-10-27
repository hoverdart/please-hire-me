import socket
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest


def test_dashboard_serves_loopback_without_reverse_dns(store,monkeypatch):
    from hireme import server
    def no_reverse_dns(*args,**kwargs):
        raise AssertionError('Local dashboard startup must not resolve hostnames')
    monkeypatch.setattr(socket,'getfqdn',no_reverse_dns)
    monkeypatch.setattr(socket,'gethostbyaddr',no_reverse_dns)
    with socket.socket() as reservation:
        reservation.bind(('127.0.0.1',0));port=reservation.getsockname()[1]
    ready=threading.Event();listeners=[];errors=[];real_server=server.LoopbackHTTPServer
    def capture(address,handler):
        listener=real_server(address,handler);listeners.append(listener);ready.set();return listener
    monkeypatch.setattr(server,'LoopbackHTTPServer',capture)
    def run():
        try:server.serve(store.root,Path.cwd(),port,token='synthetic-startup-capability')
        except BaseException as error:errors.append(error);ready.set()
    thread=threading.Thread(target=run,daemon=True);thread.start()
    try:
        assert ready.wait(5),'Local server did not bind'
        if errors:raise errors[0]
        assert listeners[0].server_name=='127.0.0.1' and listeners[0].server_port==port
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(f'http://127.0.0.1:{port}/',timeout=5) as response:
            assert response.status==200 and b'<!doctype html' in response.read().lower()
        with pytest.raises(urllib.error.HTTPError) as denied:
            opener.open(f'http://127.0.0.1:{port}/api/state',timeout=5)
        assert denied.value.code==403
        assert not errors
    finally:
        if listeners:
            shutdown=threading.Thread(target=listeners[0].shutdown,daemon=True)
            shutdown.start();shutdown.join(5)
        thread.join(5)
        assert not thread.is_alive(),'Local server did not stop'
