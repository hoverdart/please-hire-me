"""HTML rendering for batch reports. Every value is untrusted text; no remote assets."""
from __future__ import annotations

from html import escape
from urllib.parse import urlsplit

# The dashboard palette from DESIGN.md. Email clients need literal inline values.
INK = '#19233b'
MUTED = '#626d83'
RULE = '#dfe5f0'
PAPER = '#f5f7fc'
ACTION = '#3155d9'
APRICOT = '#f6b089'
TONES = {
    'confirmed': ('#e9f3ed', '#337253', 'Confirmed'),
    'attention': ('#fbf0de', '#8d641f', 'Needs you'),
    'uncertain': ('#fbe9eb', '#b73745', 'Verify first'),
    'neutral': ('#eaf0ff', '#3155d9', 'Waiting'),
    'prepared': ('#eaf0ff', '#3155d9', 'Prepared'),
}
FONT = "-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"
LOCAL_HOSTS = {'localhost', 'localhost.localdomain', '0.0.0.0', '::1', '[::1]'}


def safe_link(url):
    """An absolute public https link, or None. Dashboard and credentialed links never qualify."""
    if not isinstance(url, str) or len(url) > 2000 or any(ord(c) < 33 or ord(c) == 127 for c in url):
        return None
    try:
        parsed = urlsplit(url)
        host = (parsed.hostname or '').casefold()
        port = parsed.port
    except ValueError:
        return None
    if parsed.scheme != 'https' or not host or parsed.username or parsed.password or port not in (None, 443):
        return None
    if (host in LOCAL_HOSTS or host.startswith('127.') or host.endswith(('.local', '.localhost', '.internal'))
            or '.' not in host or host.replace('.', '').isdigit() or ':' in host):
        return None
    if 'token=' in (parsed.query + '#' + parsed.fragment).casefold():
        return None
    return url


def _text(value, limit=600):
    value = ' '.join(str(value or '').split())
    return escape(value if len(value) <= limit else value[:limit].rstrip() + '…', quote=True)


def _chip(tone):
    ground, ink, label = TONES[tone]
    return (f'<span style="display:inline-block;padding:3px 8px;border-radius:6px;background:{ground};color:{ink};'
            f'font-size:12px;font-weight:600;line-height:1.5;white-space:nowrap;">{label}</span>')


def _item(item, tone):
    link = safe_link(item.get('url'))
    parts = [f'<div style="font-size:15px;font-weight:650;color:{INK};line-height:1.35;">{_text(item.get("company"), 200)}'
             f'<span style="font-weight:400;color:{MUTED};"> — {_text(item.get("title"), 300)}</span></div>']
    if item.get('status'):
        parts.append(f'<div style="margin-top:4px;font-size:14px;color:{INK};line-height:1.5;">{_text(item["status"])}</div>')
    if item.get('detail'):
        parts.append(f'<div style="margin-top:4px;font-size:13px;color:{MUTED};line-height:1.5;">{_text(item["detail"])}</div>')
    if item.get('note'):
        ground, ink, _ = TONES['uncertain' if tone == 'uncertain' else 'attention']
        parts.append(f'<div style="margin-top:8px;padding:8px 10px;border-radius:6px;background:{ground};color:{ink};'
                     f'font-size:13px;line-height:1.5;">{_text(item["note"])}</div>')
    if item.get('questions'):
        parts.append('<ul style="margin:8px 0 0;padding:0 0 0 18px;font-size:13px;line-height:1.55;color:' + INK + ';">'
                     + ''.join(f'<li style="margin:0 0 3px;">{_text(question, 500)}</li>' for question in item['questions'][:30])
                     + '</ul>')
    if item.get('action'):
        parts.append(f'<div style="margin-top:8px;font-size:13px;line-height:1.5;color:{INK};">'
                     f'<strong style="font-weight:600;">Next action:</strong> {_text(item["action"])}</div>')
    receipt = item.get('receipt')
    if receipt:
        rows = [('Application record', receipt.get('id')), ('Confirmed at', receipt.get('confirmed_at'))]
        rows += [('Resume SHA-256', checksum) for checksum in receipt.get('resumes', [])[:4]]
        rows += [('Resume revision', revision) for revision in receipt.get('revisions', [])[:4]]
        parts.append('<table role="presentation" cellpadding="0" cellspacing="0" style="margin-top:8px;border-collapse:collapse;">'
                     + ''.join(f'<tr><td style="padding:1px 10px 1px 0;font-size:12px;color:{MUTED};vertical-align:top;white-space:nowrap;">{escape(label)}</td>'
                               f'<td style="padding:1px 0;font-size:12px;color:{INK};word-break:break-all;font-family:ui-monospace,Menlo,Consolas,monospace;">{_text(value, 100)}</td></tr>'
                               for label, value in rows if value) + '</table>')
    if link:
        parts.append(f'<div style="margin-top:8px;font-size:13px;line-height:1.5;"><a href="{escape(link, quote=True)}" '
                     f'style="color:{ACTION};font-weight:600;text-decoration:underline;">Open the posting</a></div>')
    elif item.get('url'):
        parts.append(f'<div style="margin-top:8px;font-size:12px;color:{MUTED};">Posting link withheld: it is not a public https address.</div>')
    return (f'<tr><td style="padding:14px 0;border-top:1px solid {RULE};">' + ''.join(parts) + '</td></tr>')


def _group(group):
    tone = group.get('tone') if group.get('tone') in TONES else 'neutral'
    count = group.get('count', len(group['items']))
    shown = '' if count == len(group['items']) else f' · showing {len(group["items"])}'
    return (f'<tr><td style="padding:22px 0 8px;"><table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>'
            f'<td style="font-size:16px;font-weight:650;color:{INK};line-height:1.35;">{_text(group["heading"], 200)}'
            f'<span style="font-weight:400;color:{MUTED};font-size:13px;"> · {count}{shown}</span></td>'
            f'<td align="right" style="vertical-align:top;padding-left:8px;">{_chip(tone)}</td></tr></table></td></tr>'
            + ''.join(_item(item, tone) for item in group['items']))


def _metric(value, label, tone):
    ground, ink, _ = TONES[tone]
    return (f'<td width="33%" style="padding:0 4px;vertical-align:top;"><div style="background:{ground};border-radius:8px;padding:12px 10px;">'
            f'<div style="font-size:24px;font-weight:650;color:{ink};line-height:1.1;">{int(value)}</div>'
            f'<div style="margin-top:4px;font-size:12px;color:{INK};line-height:1.4;">{escape(label)}</div></div></td>')


def _batch(model):
    rows = []
    for group in model.get('recruiting', []):
        rows.append(_group(group))
    for line in model.get('notices', []):
        rows.append(f'<tr><td style="padding:12px 0 0;"><div style="padding:10px 12px;border-radius:8px;background:{TONES["attention"][0]};'
                    f'color:{TONES["attention"][1]};font-size:13px;line-height:1.5;">{_text(line, 800)}</div></td></tr>')
    groups = [group for group in model.get('groups', []) if group['items']]
    rows.extend(_group(group) for group in groups)
    if not groups:
        rows.append(f'<tr><td style="padding:18px 0 4px;font-size:14px;color:{MUTED};line-height:1.5;">No application outcomes were recorded in this batch.</td></tr>')
    rows.extend(_group(group) for group in model.get('earlier', []) if group['items'])
    run = model.get('run', {})
    details = [f'Batch {_text(run.get("id"), 80)}', f'{_text(run.get("started"), 40)} → {_text(run.get("finished"), 40)}',
               f'Status: {_text(run.get("status"), 40)} · Mode: {_text(run.get("mode"), 40)}']
    details += [_text(line, 400) for line in model.get('diagnostics', [])]
    rows.append(f'<tr><td style="padding:18px 0 0;border-top:1px solid {RULE};font-size:12px;color:{MUTED};line-height:1.6;">'
                + '<br>'.join(details) + '</td></tr>')
    return ''.join(rows)


def render_html(models, subject):
    """One responsive, image-free document for one or more batch models."""
    totals = {key: sum(int(model.get('summary', {}).get(key, 0)) for model in models) for key in ('confirmed',)}
    # Outstanding work is a current snapshot, not additive across batches.
    latest = models[-1].get('summary', {})
    human, unconfirmed = int(latest.get('human', 0)), int(latest.get('unconfirmed', 0))
    preheader = f'{totals["confirmed"]} confirmed · {human} need you · {unconfirmed} to verify'
    batches = (f'<tr><td style="padding:10px 0 0;border-top:3px solid {RULE};"></td></tr>').join(_batch(model) for model in models)
    return ('<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<meta name="color-scheme" content="light only">'
            f'<title>{_text(subject, 200)}</title></head>'
            f'<body style="margin:0;padding:0;background:{PAPER};font-family:{FONT};color:{INK};-webkit-text-size-adjust:100%;overflow-wrap:anywhere;word-wrap:break-word;word-break:break-word;">'
            f'<div style="display:none;max-height:0;overflow:hidden;opacity:0;color:{PAPER};">{escape(preheader)}</div>'
            f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:{PAPER};table-layout:fixed;"><tr><td align="center" style="padding:24px 12px;">'
            f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:600px;table-layout:fixed;background:#ffffff;border:1px solid {RULE};border-radius:12px;">'
            f'<tr><td style="padding:22px 24px 0;"><div style="font-size:13px;font-weight:650;color:{ACTION};letter-spacing:.01em;">HireMeMaybe'
            f'<span style="display:inline-block;width:6px;height:6px;border-radius:3px;background:{APRICOT};margin-left:6px;vertical-align:middle;"></span></div>'
            f'<h1 style="margin:8px 0 0;font-size:24px;font-weight:650;letter-spacing:-.02em;line-height:1.2;color:{INK};">Application update</h1>'
            f'<div style="margin-top:6px;font-size:14px;color:{MUTED};line-height:1.5;">What ran, what the employer confirmed, and what needs you next.</div></td></tr>'
            '<tr><td style="padding:16px 20px 0;"><table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>'
            + _metric(totals['confirmed'], 'Confirmed by the employer', 'confirmed')
            + _metric(human, 'Need your input', 'attention')
            + _metric(unconfirmed, 'Uncertain — verify before any retry', 'uncertain')
            + '</tr></table></td></tr>'
            '<tr><td style="padding:0 24px 8px;"><table role="presentation" width="100%" cellpadding="0" cellspacing="0">'
            + batches + '</table></td></tr>'
            f'<tr><td style="padding:16px 24px 22px;background:{PAPER};border-top:1px solid {RULE};border-radius:0 0 12px 12px;font-size:12px;color:{MUTED};line-height:1.6;">'
            '<strong style="font-weight:600;color:' + INK + ';">Acting on this update.</strong> Posting links open the employer page. '
            'Answering questions, reconciling an uncertain result and recording a manual application happen in your private workspace: '
            'open HireMeMaybe on the computer running the worker with your saved dashboard link. This email contains no workspace link by design.<br><br>'
            'Confirmed means the employer returned a fresh receipt. An uncertain application may already exist; check with the employer before applying again. '
            'A question answered once under Your context is reused for every employer.</td></tr>'
            '</table></td></tr></table></body></html>')
