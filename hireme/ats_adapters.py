"""Small ATS boundary for posting reads and navigation; writes remain guarded."""
import html,re
from .util import Blocked

class SingleStepAdapter:
    version=1
    def navigation(self,job):return job['url'],None
    def posting_text(self,text,verified=None):
        return verified if verified is not None else re.split(r'\bApply for this job\b',text,maxsplit=1,flags=re.I)[0]

class GreenhouseAdapter(SingleStepAdapter):
    version=2
    def navigation(self,job):
        # Roblox redirects its board URL to a custom careers site. Use only
        # Greenhouse's own embedded application after verifying its public job.
        from urllib.parse import urlsplit
        path=urlsplit(job['url']).path
        match=re.fullmatch(r'/roblox/jobs/(\d+)',path)
        if not match:return super().navigation(job)
        from .net import Network
        data=Network().json('https://boards-api.greenhouse.io/v1/boards/roblox/jobs/'+match[1]+'?content=true')
        if not isinstance(data,dict) or str(data.get('id'))!=match[1] or not data.get('content') or not data.get('title'):
            raise Blocked('posting_fetch_failed','Cannot verify the Greenhouse posting for the embedded application')
        normalize=lambda value:re.sub(r'[^a-z0-9]+','',value.lower())
        if normalize(data['title'])!=normalize(job['title']):raise Blocked('posting_changed_review','The employer posting title changed')
        content=html.unescape(re.sub(r'<[^>]+>',' ',data['content']))
        return 'https://job-boards.greenhouse.io/embed/job_app?for=roblox&token='+match[1],content

def adapter_for(job):
    return GreenhouseAdapter() if 'greenhouse.io' in job['host'] else SingleStepAdapter()
