"""Small ATS boundary for posting reads and navigation; writes remain guarded."""
import html,re
from .util import Blocked

class SingleStepAdapter:
    version=1
    def navigation(self,job,*,checkpoint=None,deadline=None):return job['url'],None
    def posting_text(self,text,verified=None):
        return verified if verified is not None else re.split(r'\bApply for this job\b',text,maxsplit=1,flags=re.I)[0]

class GreenhouseAdapter(SingleStepAdapter):
    version=3
    def navigation(self,job,*,checkpoint=None,deadline=None):
        # Roblox always redirects its board URL to a custom careers site.
        from urllib.parse import urlsplit
        if re.fullmatch(r'/roblox/jobs/\d+',urlsplit(job['url']).path):return self.embedded(job,checkpoint=checkpoint,deadline=deadline)
        return super().navigation(job,checkpoint=checkpoint,deadline=deadline)

    def embedded(self,job,*,checkpoint=None,deadline=None):
        """Greenhouse's own embedded application, for boards that forward to a custom careers site.

        The custom site is outside the browser allowlist; the embedded form is
        the same Greenhouse application on a Greenhouse host, after verifying
        the public job and its title.
        """
        from urllib.parse import urlsplit
        parts=urlsplit(job['url'])
        match=re.fullmatch(r'/([A-Za-z0-9_-]+)/jobs/(\d+)',parts.path)
        if not match or parts.hostname not in {'boards.greenhouse.io','job-boards.greenhouse.io'}:
            raise Blocked('unexpected_redirect','The employer moved this application to its own careers site')
        board,job_id=match.groups()
        from .net import Network
        data=Network(checkpoint=checkpoint,deadline=deadline).json('https://boards-api.greenhouse.io/v1/boards/'+board+'/jobs/'+job_id+'?content=true')
        if not isinstance(data,dict) or str(data.get('id'))!=job_id or not data.get('content') or not data.get('title'):
            raise Blocked('posting_fetch_failed','Cannot verify the Greenhouse posting for the embedded application')
        normalize=lambda value:re.sub(r'[^a-z0-9]+','',value.lower())
        if normalize(data['title'])!=normalize(job['title']):raise Blocked('posting_changed_review','The employer posting title changed')
        content=html.unescape(re.sub(r'<[^>]+>',' ',data['content']))
        return 'https://job-boards.greenhouse.io/embed/job_app?for='+board+'&token='+job_id,content

class LeverPostingAdapter(SingleStepAdapter):
    """The /apply page omits the posting body; verify the public read before writing."""
    version=1

    def navigation(self,job,*,checkpoint=None,deadline=None):
        from urllib.parse import urlsplit
        from urllib.error import HTTPError,URLError
        from .net import Network
        parsed=urlsplit(job['url'])
        match=re.fullmatch(r'/([A-Za-z0-9_-]+)/([0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})(?:/apply)?/?',parsed.path,re.I)
        if (not match or parsed.scheme!='https' or parsed.hostname!=job['host']
                or parsed.hostname not in {'jobs.lever.co','jobs.eu.lever.co'} or parsed.port not in (None,443)
                or parsed.username or parsed.password or parsed.fragment):
            raise Blocked('posting_inspection_review','Inspect the Lever posting link')
        site,req=match.groups();api='api.eu.lever.co' if parsed.hostname=='jobs.eu.lever.co' else 'api.lever.co'
        try:data=Network(checkpoint=checkpoint,deadline=deadline).json(f'https://{api}/v0/postings/{site}/{req}?mode=json')
        except HTTPError as error:
            if error.code==404:raise Blocked('expired_posting','The official Lever posting is no longer published') from None
            if error.code in {429,500,502,503,504}:raise Blocked('posting_fetch_failed','Lever posting read temporarily failed') from None
            raise Blocked('posting_inspection_review','Lever posting read requires review') from None
        except (URLError,TimeoutError):raise Blocked('posting_fetch_failed','Lever posting read did not complete') from None
        except (ValueError,TypeError):raise Blocked('posting_inspection_review','Lever posting response is not recognized') from None
        if not isinstance(data,dict) or data.get('id')!=req or not isinstance(data.get('text'),str):
            raise Blocked('posting_inspection_review','Lever requisition identity could not be verified')
        normalize=lambda value:re.sub(r'\s+',' ',html.unescape(value)).strip().casefold()
        if normalize(data['text'])!=normalize(job['title']):raise Blocked('posting_changed_review','The employer posting title changed')
        for key,suffix in [('hostedUrl',''),('applyUrl','/apply')]:
            target=urlsplit(data.get(key,'') if isinstance(data.get(key),str) else '')
            if (target.scheme!='https' or target.hostname!=parsed.hostname or target.port not in (None,443)
                    or target.username or target.password or target.path.rstrip('/')!=f'/{site}/{req}'+suffix):
                raise Blocked('posting_changed_review','Lever response refers to a different posting')
        body=data.get('descriptionPlain');lists=data.get('lists',[])
        if not isinstance(body,str) or not body.strip() or not isinstance(lists,list):
            raise Blocked('posting_inspection_review','The official Lever posting body is missing')
        parts=[body]
        for item in lists:
            if not isinstance(item,dict) or not isinstance(item.get('text'),str) or not isinstance(item.get('content'),str):
                raise Blocked('posting_inspection_review','Lever posting requirements are not recognized')
            parts.extend([item['text'],html.unescape(re.sub(r'<[^>]+>',' ',item['content']))])
        for key in ('additionalPlain','salaryDescriptionPlain'):
            value=data.get(key)
            if isinstance(value,str):parts.append(value)
        if checkpoint:checkpoint()
        return job['url'],'\n'.join(parts)


class WorkdayPostingAdapter(SingleStepAdapter):
    """Inspect a configured public posting; this grants no account/draft writes."""
    version=1

    def navigation(self,job,*,checkpoint=None,deadline=None):
        from urllib.parse import urlsplit
        from urllib.error import HTTPError,URLError
        import socket
        from .portals import WORKDAY
        from .net import Network
        configured=next((row for row in WORKDAY if row[4]==job['host']),None)
        if not configured:raise Blocked('posting_inspection_review','Workday career site is not configured')
        _,tenant,_,site,host=configured
        def posting_path(url):
            parsed=urlsplit(url)
            if (parsed.scheme!='https' or parsed.hostname!=host or parsed.port not in (None,443)
                    or parsed.username or parsed.password or parsed.query or parsed.fragment):
                raise ValueError('Posting URL does not match its configured career site')
            # Known public path shape. Unknown locale/path/escaped segments require
            # inspection instead of constructing a speculative employer endpoint.
            match=re.fullmatch(r'/(?:en-US/)?'+re.escape(site)+r'(/job/[A-Za-z0-9._~-]+/[A-Za-z0-9._~-]+)',parsed.path)
            if not match or any(part in {'.','..'} for part in parsed.path.split('/')):
                raise ValueError('Posting path is unrecognized')
            return match[1]
        try:path=posting_path(job['url'])
        except ValueError:raise Blocked('posting_inspection_review','Inspect the configured Workday posting link') from None
        endpoint=f'https://{host}/wday/cxs/{tenant}/{site}'+path
        try:data=Network(checkpoint=checkpoint,deadline=deadline).json(endpoint)
        except Blocked:raise
        except HTTPError as error:
            if error.code in {500,502,503,504}:raise Blocked('posting_fetch_failed','Employer posting read temporarily failed') from None
            raise Blocked('posting_inspection_review','Employer posting read requires review (HTTP '+str(error.code)+')') from None
        except (URLError,TimeoutError) as error:
            reason=error.reason if isinstance(error,URLError) else error
            if isinstance(reason,(TimeoutError,socket.gaierror,ConnectionResetError,ConnectionAbortedError)):
                raise Blocked('posting_fetch_failed','Employer posting read did not complete') from None
            raise Blocked('posting_inspection_review','Employer posting connection requires inspection') from None
        except (ValueError,TypeError):
            raise Blocked('posting_inspection_review','Employer posting response is not recognized') from None
        posting=data.get('jobPostingInfo') if isinstance(data,dict) else None
        if not isinstance(posting,dict):raise Blocked('posting_inspection_review','Employer posting response is not recognized')
        title=posting.get('title');description=posting.get('jobDescription');req=posting.get('jobReqId')
        if (not isinstance(title,str) or not title.strip() or not isinstance(description,str) or not description.strip()
                or not isinstance(req,str) or not re.fullmatch(r'[A-Za-z0-9-]+',req)
                or not path.rsplit('/',1)[1].endswith('_'+req)
                or posting.get('jobPostingId')!=path.rsplit('/',1)[1] or posting.get('jobPostingSiteId')!=site):
            raise Blocked('posting_inspection_review','Employer requisition and career-site identity could not be verified')
        try:external_path=posting_path(posting.get('externalUrl',''))
        except (ValueError,TypeError,AttributeError):raise Blocked('posting_inspection_review','Employer posting destination could not be verified') from None
        if external_path!=path:raise Blocked('posting_changed_review','Employer response refers to a different posting')
        normalize=lambda value:re.sub(r'\s+',' ',html.unescape(value)).strip().casefold()
        if normalize(title)!=normalize(job['title']):raise Blocked('posting_changed_review','Employer posting title changed; review before preparing')
        if posting.get('canApply') is False:raise Blocked('expired_posting','Employer reports that this posting no longer accepts applications')
        if posting.get('canApply') is not True:raise Blocked('posting_inspection_review','Employer posting availability could not be verified')
        content=html.unescape(re.sub(r'<[^>]+>',' ',description))
        if not content.strip():raise Blocked('posting_inspection_review','Employer posting text is empty')
        if checkpoint:checkpoint()
        return job['url'],content

def adapter_for(job):
    if 'greenhouse.io' in job['host']:return GreenhouseAdapter()
    if job['host'] in {'jobs.lever.co','jobs.eu.lever.co'}:return LeverPostingAdapter()
    if job['host'].endswith('.myworkdayjobs.com'):return WorkdayPostingAdapter()
    return SingleStepAdapter()
