from __future__ import annotations

import concurrent.futures
import html
import json
import re
import time
from pathlib import Path
from urllib.parse import urlsplit

from .net import Network,SafeRedirect
from .util import canonical_url,digest,now,Blocked

ATS_HOSTS={"jobs.ashbyhq.com","boards.greenhouse.io","job-boards.greenhouse.io","boards.eu.greenhouse.io",
 "job-boards.eu.greenhouse.io","jobs.lever.co","jobs.eu.lever.co","apply.workable.com","jobs.smartrecruiters.com"}
PORTAL_HOSTS={"www.amazon.jobs","amazon.jobs","www.google.com","www.deshaw.com","apply.deshaw.com",
 "careers.twosigma.com","bloomberg.avature.net","apply.careers.microsoft.com","careers.qualcomm.com",
 "explore.jobs.netflix.net","career.mlp.com","careers.amd.com","careers-amd.icims.com","jobs.uber.com",
 "www.rentec.com","jobs.apple.com","nvidia.wd5.myworkdayjobs.com","salesforce.wd12.myworkdayjobs.com",
 "intel.wd1.myworkdayjobs.com","arrowstreetcapital.wd5.myworkdayjobs.com","gresearch.wd103.myworkdayjobs.com"}


def posting(url,company,title,location,source,description="",**extra):
    url=canonical_url(url); host=urlsplit(url).hostname
    if host not in ATS_HOSTS|PORTAL_HOSTS: raise ValueError("Unapproved application host")
    if not all(isinstance(x,str) and x.strip() for x in (company,title)):
        raise ValueError("Invalid company/title")
    # Req URL, not title or slug guesses, is the durable identity. Alias URLs can be explicitly reconciled.
    return {"id":digest(url),"url":url,"host":host,"company":company[:200],"title":title[:500],
            "location":location[:1000],"source":source,"description":html.unescape(re.sub(r"<[^>]+>"," ",description))[:100000],**extra}


def board_sources(repo,ats=None,limit=None,store=None):
    slugs=set()
    for line in (repo / "data/boards.md").read_text().splitlines():
        if line.startswith("|"):
            slugs.update(re.findall(r"`([A-Za-z0-9._-]+)`",line))
    for line in (repo / "data/slug-candidates.txt").read_text().splitlines():
        if re.fullmatch(r"[A-Za-z0-9._-]{1,100}",line.strip()): slugs.add(line.strip())
    if store:
        for row in store.db.execute("SELECT url FROM jobs"):
            p=urlsplit(row[0]);parts=p.path.strip("/").split("/")
            if p.hostname in ATS_HOSTS and parts and re.fullmatch(r"[A-Za-z0-9._-]{1,100}",parts[0]):slugs.add(parts[0])
    if limit: slugs=set(sorted(slugs)[:limit])
    return [(a,s) for s in sorted(slugs) for a in (ats or ("ash","gh","lv"))]


def probe(net,ats,slug):
    out=[]
    if ats=="ash":
        d=net.json(f"https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true")
        for j in d.get("jobs",[]):
            if not j.get("isListed",True): continue
            out.append(posting(j["jobUrl"],slug,j["title"],j.get("location","")+"; "+"; ".join(x.get("location","") for x in j.get("secondaryLocations",[])),f"ash:{slug}",j.get("descriptionPlain", "")))
    elif ats=="gh":
        d=net.json(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true")
        for j in d.get("jobs",[]):
            url=j["absolute_url"]
            if urlsplit(url).hostname not in ATS_HOSTS:
                # The official board API identifies the same requisition even when its
                # public link points at a custom careers website.
                if not str(j.get("id","")).isdigit(): raise ValueError("Invalid Greenhouse requisition ID")
                url=f"https://job-boards.greenhouse.io/{slug}/jobs/{j['id']}"
            out.append(posting(url,slug,j["title"],j.get("location",{}).get("name", ""),f"gh:{slug}",j.get("content", "")))
    elif ats=="lv":
        d=net.json(f"https://api.lever.co/v0/postings/{slug}?mode=json")
        if not isinstance(d,list): raise ValueError("Invalid Lever response")
        for j in d:
            sr=j.get("salaryRange") or {}
            comp={"min":sr.get("min"),"max":sr.get("max"),"currency":sr.get("currency"),"period":sr.get("interval")}
            out.append(posting(j.get("hostedUrl") or j["applyUrl"],slug,j["text"],j.get("categories",{}).get("location", ""),f"lv:{slug}",j.get("descriptionPlain", "")+" "+" ".join(x.get("content", "") for x in j.get("lists",[])),compensation=comp))
    elif ats=="sr":
        offset=0
        while offset<1000:
            d=net.json(f"https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=100&offset={offset}")
            for j in d.get("content",[]):
                loc=j.get("location") or {}
                out.append(posting(f"https://jobs.smartrecruiters.com/{slug}/{j['id']}",slug,j["name"],"; ".join(str(loc.get(k) or "") for k in ("city","region","country")),f"sr:{slug}"))
            if len(d.get("content",[]))<100: break
            offset+=100
    elif ats=="wk":
        d=net.json(f"https://apply.workable.com/api/v1/widget/accounts/{slug}?details=true")
        for j in d.get("jobs",[]):
            out.append(posting(j.get("url") or j["application_url"],slug,j["title"],"; ".join(str(j.get(k) or "") for k in ("city","state","country")),f"wk:{slug}",(j.get("description") or "")+" "+(j.get("requirements") or "")))
    else: raise ValueError("Unsupported ATS")
    return out


def source_result(store,sid,jobs=None,error=None):
    # Full snapshots eliminate lossy global timestamp watermarks; partial failures keep existing jobs.
    if jobs is not None:
        for job in jobs: store.upsert_job(job)
    store.db.execute("INSERT INTO sources VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET status=excluded.status,checked=excluded.checked,error=excluded.error,payload=excluded.payload",
                     (sid,"error" if error else "ok",now(),str(error or "")[:500],json.dumps({"jobs":len(jobs or [])})))
    results=getattr(store,'discovery_results',None)
    if results is not None:results[sid]='error' if error else 'ok'


def sweep_boards(store,repo,ats=None,limit=None,deadline_seconds=1800):
    sources=board_sources(repo,ats,limit,store)
    # Failed and least recently checked sources get the next slots; no alphabetical starvation.
    checks={r["id"]:r["checked"] for r in store.db.execute("SELECT * FROM sources")}
    sources.sort(key=lambda x:checks.get(":".join(x),""))
    net=Network(time.monotonic()+deadline_seconds)
    n=0
    with concurrent.futures.ThreadPoolExecutor(max_workers=store.settings()["discovery_workers"]) as pool:
        for offset in range(0,len(sources),store.settings()["discovery_workers"]):
            store.checkpoint()
            if time.monotonic()>net.deadline: break
            batch={pool.submit(probe,net,a,s):(a,s) for a,s in sources[offset:offset+store.settings()["discovery_workers"]]}
            for future in concurrent.futures.as_completed(batch):
                store.checkpoint()
                sid=":".join(batch[future])
                try:
                    jobs=future.result(); source_result(store,sid,jobs); n+=len(jobs)
                except Exception as e: source_result(store,sid,error=f"{type(e).__name__}: {e}")
    return n


def sweep_lists(store,net=None):
    import http.cookiejar
    import urllib.request
    net=net or Network(time.monotonic()+600,checkpoint=store.checkpoint)
    for name,repo in (("new-grad","SimplifyJobs/New-Grad-Positions"),("internships","SimplifyJobs/Summer2027-Internships")):
        store.checkpoint()
        jobs=[]; sid="simplify:"+name
        try:
            data=net.json(f"https://raw.githubusercontent.com/{repo}/dev/.github/scripts/listings.json")
            for j in data:
                if not j.get("active") or not j.get("is_visible",True): continue
                try: jobs.append(posting(j["url"],j["company_name"],j["title"],"; ".join(j.get("locations") or []),sid,
                                        "Sponsorship: "+str(j.get("sponsorship") or ""),terms=j.get("terms") or []))
                except (ValueError,KeyError,TypeError): continue
            source_result(store,sid,jobs)
        except Blocked as e:
            if e.reason=="paused":raise
            source_result(store,sid,error=e)
        except Exception as e: source_result(store,sid,error=e)
    boards=(("a16z","portfoliojobs.a16z.com","andreessen-horowitz"),("Sequoia","jobs.sequoiacap.com","sequoia-capital"),
        ("Lightspeed","jobs.lsvp.com","lightspeed"),("Kleiner Perkins","jobs.kleinerperkins.com","kleiner-perkins"),
        ("GV","jobs.gv.com","gv"),("Bessemer","jobs.bvp.com","bessemer-ventures"))
    for label,host,board in boards:
        store.checkpoint()
        sid="consider:"+label; jobs=[]
        try:
            jar=http.cookiejar.CookieJar()
            op=urllib.request.build_opener(SafeRedirect(),urllib.request.HTTPCookieProcessor(jar),urllib.request.HTTPSHandler(context=net.context))
            page=net.fetch(f"https://{host}/jobs",opener=op)
            token=re.search(r'"csrfToken":"([^"]+)"',page)
            if not token: raise ValueError("CSRF token missing; source changed")
            seq=None
            for _ in range(20):
                store.checkpoint()
                body={"meta":{"size":100,**({"sequence":seq} if seq else {})},"board":{"id":board,"isParent":True},"query":{},"grouped":False}
                d=json.loads(net.fetch(f"https://{host}/api-boards/search-jobs",body,{"x-csrf-token":token[1]},op))
                for j in d.get("jobs") or []:
                    try:
                        sal=j.get("salary") or {}; period=(sal.get("period") or {}).get("value")
                        jobs.append(posting(j.get("applyUrl") or j["url"],j["companyName"],j["title"],"; ".join(j.get("locations") or []),sid,
                           j.get("description") or "",min_years=j.get("minYearsExp"),compensation={"min":sal.get("minValue"),"max":sal.get("maxValue"),"period":period,"currency":sal.get("currency")}))
                    except (ValueError,KeyError,TypeError): continue
                # Persist every page, even if the next page fails.
                source_result(store,sid,jobs); jobs=[]
                next_seq=(d.get("meta") or {}).get("sequence")
                if not next_seq or next_seq==seq or not d.get("jobs"): break
                seq=next_seq
        except Blocked as e:
            if e.reason=="paused":raise
            source_result(store,sid,jobs,error=e)
        except Exception as e: source_result(store,sid,jobs,error=e)


def sweep_portals(store,net=None):
    from .portals import collect
    net=net or Network(time.monotonic()+600,checkpoint=store.checkpoint)
    store.checkpoint()
    rows,errors=collect(net)
    store.checkpoint()
    for r in rows:
        store.checkpoint()
        try: store.upsert_job(posting(r[5],r[0],r[3],r[4],"portal:"+r[0]))
        except (ValueError,TypeError): continue
    source_result(store,"portals",[],error="; ".join(errors) if errors else None)
