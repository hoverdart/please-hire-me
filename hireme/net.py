from __future__ import annotations

import json
import ssl
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from .util import Blocked, canonical_url, public_host

MAX_BYTES=32*1024*1024


class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        canonical_url(newurl)
        if not public_host(urlsplit(newurl).hostname): raise Blocked("private_network_denied")
        return super().redirect_request(req,fp,code,msg,headers,newurl)


class Network:
    def __init__(self,deadline=None,checkpoint=None):
        self.checkpoint=checkpoint
        self.deadline=deadline or time.monotonic()+1800
        self.context=ssl.create_default_context()
        self.opener=urllib.request.build_opener(SafeRedirect(),urllib.request.HTTPSHandler(context=self.context))

    def fetch(self,url,data=None,headers=None,opener=None):
        if self.checkpoint:self.checkpoint()
        canonical_url(url)
        if not public_host(urlsplit(url).hostname): raise Blocked("private_network_denied")
        if time.monotonic()>self.deadline: raise Blocked("discovery_deadline")
        req=urllib.request.Request(url,data=json.dumps(data).encode() if data is not None else None,
             headers={"User-Agent":"please-hire-me/0.3 (+job discovery)",**({"Content-Type":"application/json"} if data is not None else {}),**(headers or {})})
        for attempt in range(2):
            if self.checkpoint:self.checkpoint()
            if time.monotonic()>self.deadline: raise Blocked("discovery_deadline")
            try:
                with (opener or self.opener).open(req,timeout=min(25,max(1,self.deadline-time.monotonic()))) as response:
                    content=response.read(MAX_BYTES+1)
                    if len(content)>MAX_BYTES: raise Blocked("response_too_large")
                    return content.decode("utf-8",errors="replace")
            except urllib.error.HTTPError as e:
                if e.code not in (429,500,502,503,504) or attempt: raise
                time.sleep(2)
        raise Blocked("network_error")

    def json(self,url,data=None):
        return json.loads(self.fetch(url,data))
