"""Bounded document styles shared by resumes and letters.

A style changes presentation only. Text, claims and their order within an
entry come from applicant-confirmed sources. A reference PDF contributes
measurements such as font size and margins; none of its words are kept.
"""
from __future__ import annotations

import io
import re
from html import escape
from pathlib import Path

from .util import Blocked

SECTIONS = ('education', 'experience', 'projects', 'skills')
SECTION_TITLES = {'education': 'Education', 'experience': 'Experience', 'projects': 'Projects', 'skills': 'Skills'}
INK = '#19233b'
TEMPLATES = {
    # A plain single column with every value on its own text line, for applicant tracking systems.
    'ats': {'font': 'sans', 'body_size': 10.5, 'margin': 54, 'line_spacing': 1.35, 'accent': '#000000',
            'heading_case': 'upper', 'date_layout': 'inline', 'rule': True, 'max_bullets': 4, 'max_pages': 2,
            'section_order': ['education', 'experience', 'projects', 'skills']},
    # Restrained colour and right-aligned dates for a reader.
    'modern': {'font': 'sans', 'body_size': 10, 'margin': 50, 'line_spacing': 1.4, 'accent': '#3155d9',
               'heading_case': 'title', 'date_layout': 'right', 'rule': True, 'max_bullets': 4, 'max_pages': 2,
               'section_order': ['experience', 'projects', 'education', 'skills']},
    # Dense serif layout for technical and research roles.
    'compact': {'font': 'serif', 'body_size': 9.5, 'margin': 40, 'line_spacing': 1.25, 'accent': '#19233b',
                'heading_case': 'upper', 'date_layout': 'right', 'rule': True, 'max_bullets': 5, 'max_pages': 1,
                'section_order': ['education', 'skills', 'experience', 'projects']},
}
BOUNDS = {'body_size': (9.0, 12.0), 'margin': (36.0, 72.0), 'line_spacing': (1.15, 1.6), 'max_bullets': (1, 8), 'max_pages': (1, 2)}
DEFAULT_STYLE = {'template': 'ats', 'mode': 'select', **TEMPLATES['ats']}
FONT_FILES = {'sans': ('DejaVuSans.ttf', 'DejaVuSans-Bold.ttf', 'DejaVuSans-Oblique.ttf'),
              'serif': ('DejaVuSerif.ttf', 'DejaVuSerif-Bold.ttf', 'DejaVuSerif-Italic.ttf')}


def _luminance(color):
    channels = [int(color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def validate_style(style):
    """Return a complete style, rejecting anything outside the readable bounds."""
    if style is None:
        style = {}
    if not isinstance(style, dict):
        raise ValueError('Resume style must be a group of settings')
    template = style.get('template', 'ats')
    if not isinstance(template,str) or template not in (*TEMPLATES, 'reference'):
        raise ValueError('Choose the ATS, modern, compact or reference resume template')
    known = set(DEFAULT_STYLE) | {'reference_hash'}
    if set(style) - known:
        raise ValueError('Unknown resume style setting')
    result = {'template': template, 'mode': 'select', **TEMPLATES['ats' if template == 'reference' else template]}
    result.update(style)
    if result['mode'] not in ('select', 'rewrite'):
        raise ValueError('Choose exact selection or grounded rewriting')
    if not isinstance(result['font'],str) or result['font'] not in FONT_FILES:
        raise ValueError('Choose a sans or serif resume font')
    if result['heading_case'] not in ('upper', 'title') or result['date_layout'] not in ('inline', 'right'):
        raise ValueError('Invalid resume heading or date layout')
    if type(result['rule']) is not bool:
        raise ValueError('Invalid resume heading rule')
    for key, (low, high) in BOUNDS.items():
        value = result[key]
        integral = key in ('max_bullets', 'max_pages')
        if type(value) not in ((int,) if integral else (int, float)) or not low <= value <= high:
            raise ValueError(f'Resume {key.replace("_", " ")} must be between {low:g} and {high:g}')
    accent = result['accent']
    if not isinstance(accent, str) or not re.fullmatch(r'#[0-9a-fA-F]{6}', accent):
        raise ValueError('Resume accent must be a #rrggbb colour')
    result['accent'] = accent.lower()
    # WCAG AA for normal text against white paper.
    if 1.05 / (_luminance(result['accent']) + 0.05) < 4.5:
        raise ValueError('Choose a darker resume accent; this colour is hard to read on white paper')
    order = result['section_order']
    if not isinstance(order, list) or any(not isinstance(key,str) for key in order) or sorted(order) != sorted(SECTIONS):
        raise ValueError('Resume section order must list education, experience, projects and skills once each')
    result['section_order'] = list(order)
    if template == 'reference':
        if not isinstance(result.get('reference_hash'), str) or not re.fullmatch(r'[0-9a-f]{64}', result['reference_hash']):
            raise ValueError('Analyse a reference resume before choosing the reference template')
    else:
        result.pop('reference_hash', None)
    return result


def _fonts(family):
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    names = tuple(f'Hireme{family.title()}{suffix}' for suffix in ('', 'Bold', 'Italic'))
    if names[0] not in pdfmetrics.getRegisteredFontNames():
        directory = Path(__file__).parent / 'resources' / 'fonts'
        for name, filename in zip(names, FONT_FILES[family]):
            pdfmetrics.registerFont(TTFont(name, str(directory / filename)))
        pdfmetrics.registerFontFamily(names[0], normal=names[0], bold=names[1], italic=names[2], boldItalic=names[1])
    return names


def require_glyphs(texts, family, reason='resume_font_unsupported'):
    """Every visible character must exist in each embedded face; nothing is substituted or dropped."""
    from reportlab.pdfbase import pdfmetrics
    faces = [pdfmetrics.getFont(name).face.charToGlyph for name in _fonts(family)]
    missing = sorted({char for text in texts for char in text if not char.isspace()
                      and any(ord(char) not in face for face in faces)})
    if missing:
        raise Blocked(reason, 'These characters are not in the embedded document font: ' + ' '.join(missing[:12])
                      + '. Use your original resume for this application, or adjust the confirmed wording.')


def link_target(value):
    """A safe mailto/https target for a confirmed contact value, or None."""
    value = value.strip()
    if re.fullmatch(r'[^\s@<>"]+@[^\s@<>"]+\.[^\s@<>"]+', value):
        return 'mailto:' + value
    candidate = value if re.match(r'https?://', value, re.I) else 'https://' + value
    if re.fullmatch(r'https://[A-Za-z0-9.-]+\.[A-Za-z]{2,}(?:/[^\s<>"\']*)?', candidate) and '..' not in candidate:
        return candidate
    return None


def display_link(value):
    return re.sub(r'^https?://(?:www\.)?', '', value.strip(), flags=re.I).rstrip('/')


def _styles(style):
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_RIGHT
    from reportlab.lib.styles import ParagraphStyle
    regular, bold, italic = _fonts(style['font'])
    size = float(style['body_size'])
    leading = size * float(style['line_spacing'])
    ink = colors.HexColor('#000000' if style['template'] == 'ats' else INK)
    accent = colors.HexColor(style['accent'])
    body = ParagraphStyle('body', fontName=regular, fontSize=size, leading=leading, textColor=ink, spaceAfter=size * 0.2,
                          splitLongWords=1)
    return {
        'body': body,
        'bullet': ParagraphStyle('bullet', parent=body, leftIndent=size * 1.1, firstLineIndent=0, bulletIndent=0,
                                 bulletFontName=regular, bulletFontSize=size),
        'meta': ParagraphStyle('meta', parent=body, fontName=italic, textColor=ink),
        'right': ParagraphStyle('right', parent=body, alignment=TA_RIGHT, fontName=italic, spaceBefore=size * 0.45, spaceAfter=size * 0.1),
        'entry': ParagraphStyle('entry', parent=body, fontName=bold, spaceBefore=size * 0.45, spaceAfter=size * 0.1),
        'section': ParagraphStyle('section', parent=body, fontName=bold, fontSize=size + 1.5, leading=(size + 1.5) * 1.25,
                                  textColor=accent, spaceBefore=size * 0.9, spaceAfter=size * 0.25, keepWithNext=True),
        'name': ParagraphStyle('name', parent=body, fontName=bold, fontSize=size + 8, leading=(size + 8) * 1.2,
                               textColor=accent, spaceAfter=size * 0.3),
        'contact': ParagraphStyle('contact', parent=body, fontSize=max(9.0, size - 0.5), leading=max(9.0, size - 0.5) * 1.35,
                                  spaceAfter=size * 0.2),
        'accent': accent, 'size': size,
    }


def _section_heading(title, style, styles, width):
    from reportlab.platypus import HRFlowable, Paragraph
    text = title.upper() if style['heading_case'] == 'upper' else title
    flowables = [Paragraph(escape(text), styles['section'])]
    if style['rule']:
        flowables.append(HRFlowable(width=width, thickness=0.6, color=styles['accent'], spaceBefore=0, hAlign='LEFT',
                                    spaceAfter=styles['size'] * 0.35))
    return flowables


def _entry_block(entry, style, styles, width):
    """One employer/role or school with its own dates.

    Returns the flowables that must share a page (heading, dates and first
    line) and the lines that may continue on the next page.
    """
    from reportlab.pdfbase.pdfmetrics import stringWidth
    from reportlab.platypus import Paragraph, Table, TableStyle
    heading, meta, dates = entry.get('heading', ''), entry.get('meta', ''), entry.get('dates', '')
    target = link_target(entry['url']) if entry.get('url') else None
    meta_markup = f'<link href="{escape(target, quote=True)}">{escape(meta)}</link>' if target else escape(meta)
    block = []
    if heading and dates and style['date_layout'] == 'right':
        # The date column is as wide as its text, so a date range stays on one line.
        date_width = min(width * 0.45, stringWidth(dates, styles['right'].fontName, styles['right'].fontSize) + 6)
        row = Table([[Paragraph(escape(heading), styles['entry']), Paragraph(escape(dates), styles['right'])]],
                    colWidths=[width - date_width, date_width], hAlign='LEFT')
        row.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'TOP'), ('FONTNAME', (0, 0), (-1, -1), styles['body'].fontName),
                                 ('LEFTPADDING', (0, 0), (-1, -1), 0),
                                 ('RIGHTPADDING', (0, 0), (-1, -1), 0), ('TOPPADDING', (0, 0), (-1, -1), 0),
                                 ('BOTTOMPADDING', (0, 0), (-1, -1), 0)]))
        block.append(row)
        if meta:
            block.append(Paragraph(meta_markup, styles['meta']))
    else:
        if heading:
            block.append(Paragraph(escape(heading), styles['entry']))
        line = ' | '.join(value for value in (meta_markup, escape(dates)) if value)
        if line:
            block.append(Paragraph(line, styles['meta']))
    lines = [Paragraph(escape(line), styles['bullet'], bulletText='•') if entry.get('bulleted') else
             Paragraph(escape(line), styles['body']) for line in entry.get('lines', [])]
    return block + lines[:1], lines[1:]


def plain_text(content, style=None):
    """Every string that must survive rendering, in the page's reading order."""
    parts = [content['name'], *content.get('contact', [])]
    right = bool(style) and style['date_layout'] == 'right'
    for key, entries in content['sections']:
        title = SECTION_TITLES.get(key, key)
        parts.append(title.upper() if style and style['heading_case'] == 'upper' else title)
        for entry in entries:
            heading, meta, dates = entry.get('heading', ''), entry.get('meta', ''), entry.get('dates', '')
            parts.extend([heading, dates, meta] if right and heading and dates else [heading, meta, dates])
            parts.extend(entry.get('lines', []))
    return [part for part in parts if part]


def render_resume(content, style):
    """Build the PDF, then prove every confirmed string is present, selectable and in order."""
    from pypdf import PdfReader
    from reportlab.platypus import Paragraph, SimpleDocTemplate
    style = validate_style(style)
    styles = _styles(style)
    plain = plain_text(content, style)
    require_glyphs(plain + ['•|'], style['font'])
    margin = float(style['margin'])
    width = 612 - 2 * margin
    from reportlab.platypus import KeepTogether
    story = [Paragraph(escape(content['name']), styles['name'])]
    contact = []
    for value in content.get('contact', []):
        target = link_target(value) if re.search(r'@|\.[a-z]{2,}', value, re.I) and not re.search(r'\s', value) else None
        contact.append(f'<link href="{escape(target, quote=True)}">{escape(value)}</link>' if target else escape(value))
    if contact:
        story.append(Paragraph(' | '.join(contact), styles['contact']))
    for key, entries in content['sections']:
        # A section heading never sits alone at the foot of a page.
        lead = _section_heading(SECTION_TITLES.get(key, key), style, styles, width)
        for entry in entries:
            together, rest = _entry_block(entry, style, styles, width)
            story.append(KeepTogether(lead + together))
            story.extend(rest)
            lead = []
        story.extend(lead)
    stream = io.BytesIO()
    SimpleDocTemplate(stream, pagesize=(612, 792), leftMargin=margin - 6, rightMargin=margin - 6, topMargin=margin - 6,
                      bottomMargin=margin - 6, author=content['name'], title='Resume — ' + content['name'],
                      subject='Resume', creator='HireMeMaybe', initialFontName=_fonts(style['font'])[0]).build(story)
    data = stream.getvalue()
    reader = PdfReader(io.BytesIO(data))
    pages = len(reader.pages)
    if pages > style['max_pages']:
        raise Blocked('resume_too_long', f'This resume needs {pages} pages and the style allows {style["max_pages"]}. '
                      'Lower the bullets per role, reduce spacing within the allowed range, allow two pages, or use your original resume. '
                      'Nothing was removed to make it fit.')
    squash = lambda text: re.sub(r'\s+', '', text)
    extracted = squash(''.join(page.extract_text() or '' for page in reader.pages))
    position = 0
    for text in plain:
        # Text must be present after the previous item: complete, and in reading order.
        found = extracted.find(squash(text), position)
        if found < 0:
            raise Blocked('resume_render_failed', 'The generated PDF did not keep every confirmed line in reading order. Use your original resume.')
        position = found + len(squash(text))
    return data, '\n'.join(plain), {'pages': pages, 'bytes': len(data)}


def style_from_reference(data):
    """Measure a reference resume's typography and spacing. No reference text is returned."""
    import hashlib
    from collections import Counter
    from pypdf import PdfReader
    if not data.startswith(b'%PDF-') or len(data) > 20 * 1024 * 1024:
        raise ValueError('Choose a PDF resume up to 20 MiB as the style reference')
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted or not 1 <= len(reader.pages) <= 4:
            raise ValueError('Choose an unencrypted reference resume of one to four pages')
        page = reader.pages[0]
        page_width = float(page.mediabox.width)
        runs = []

        def visit(text, cm, tm, font, size):
            if text and text.strip() and size:
                # Effective size and position include the text and page transforms.
                scale = abs(tm[3] * cm[3]) or abs(tm[0] * cm[0]) or 1
                x = tm[4] * cm[0] + cm[4]
                y = tm[5] * cm[3] + cm[5]
                name = str((font or {}).get('/BaseFont', '')) if font else ''
                runs.append((round(float(size) * scale, 1), x, y, name, text.strip()))

        page.extract_text(visitor_text=visit)
    except ValueError:
        raise
    except Exception:
        raise ValueError('The reference PDF could not be read. Export it again as a text PDF') from None
    runs = [run for run in runs if 5 <= run[0] <= 40]
    if len(runs) < 8:
        raise ValueError('The reference PDF has too little selectable text to measure. Export it as a text PDF, not a scan')
    weights = Counter()
    for size, _, _, _, text in runs:
        weights[size] += len(text)
    body = weights.most_common(1)[0][0]
    clamp = lambda value, key: min(BOUNDS[key][1], max(BOUNDS[key][0], value))
    left = min(run[1] for run in runs)
    baselines = sorted({round(run[2], 1) for run in runs if abs(run[0] - body) < 0.3}, reverse=True)
    gaps = [a - b for a, b in zip(baselines, baselines[1:]) if body * 1.0 <= a - b <= body * 1.9]
    # Lines inside a paragraph sit closer than paragraphs do; the smaller gaps are the line spacing.
    spacing = sorted(gaps)[len(gaps) // 5] / body if gaps else 1.35
    serif_pattern = r'times|georgia|garamond|cambria|charter|palatino|baskerville|cmr\d|computermodern|minion|libertine|merriweather|sfrm|serif'
    is_serif = lambda name: bool(re.search(serif_pattern, name.casefold())) and 'sans' not in name.casefold()
    serif = sum(len(run[4]) for run in runs if is_serif(run[3])) > sum(len(run[4]) for run in runs) / 2
    # Case of short, larger-or-equal lines hints at the heading treatment.
    headings = [run[4] for run in runs if run[0] >= body and len(run[4]) <= 24 and run[4].replace(' ', '').isalpha()]
    upper = sum(text.isupper() for text in headings) > len(headings) / 2 if headings else False
    order = []
    for _, _, _, _, text in sorted(runs, key=lambda run: -run[2]):
        key = next((section for section, pattern in (('education', r'^education\b'), ('experience', r'^(work |professional |research )?experience\b|^employment\b'),
                                                    ('projects', r'^(selected |personal )?projects\b'), ('skills', r'^(technical )?skills\b'))
                    if re.match(pattern, text.casefold())), None)
        if key and key not in order and len(text) <= 40:
            order.append(key)
    order += [section for section in TEMPLATES['ats']['section_order'] if section not in order]
    measured = {'template': 'reference', 'reference_hash': hashlib.sha256(data).hexdigest(),
                'font': 'serif' if serif else 'sans',
                'body_size': round(clamp(body, 'body_size') * 2) / 2,
                'margin': float(round(clamp(left, 'margin'))),
                'line_spacing': round(clamp(spacing, 'line_spacing'), 2),
                'heading_case': 'upper' if upper else 'title',
                'date_layout': 'right' if any(run[1] > page_width * 0.62 and re.search(r'(19|20)\d{2}', run[4]) for run in runs) else 'inline',
                'section_order': order, 'max_pages': min(2, len(reader.pages))}
    notes = []
    if body != measured['body_size']:
        notes.append(f'The reference body text is {body:g} pt; the readable range is {BOUNDS["body_size"][0]:g}–{BOUNDS["body_size"][1]:g} pt, so {measured["body_size"]:g} pt is used.')
    if round(left) != measured['margin']:
        notes.append(f'The reference margin is {left:.0f} pt; {measured["margin"]:.0f} pt is used to stay printable.')
    notes.append('Typeface: an embedded ' + measured['font'] + ' face is used so every installation produces the same PDF. The reference typeface itself is not copied.')
    return validate_style(measured), notes
