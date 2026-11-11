"""A reviewable profile of the applicant's writing voice, measured from approved samples.

The profile describes how the applicant writes: sentence length, directness,
punctuation and openings. It carries no claims; a writing sample establishes
voice, never a qualification. Preferences are the applicant's own corrections
and persist until changed. Nothing here calls a model.
"""
from __future__ import annotations

import json
import re
from statistics import median

from .util import digest, now

# Stock application phrases. A draft may use one only when the applicant's own samples do.
GENERIC = ('excited to apply', 'thrilled', 'passionate about', 'i am passionate', 'fast-paced', 'dream job', 'perfect fit', 'great fit',
           'ideal candidate', 'hit the ground running', 'team player', 'synergy', 'leverage my', 'utilize my', 'cutting-edge',
           'world-class', 'i am confident that', 'i believe i would be', 'i would be honored', 'esteemed', 'look no further',
           'unique opportunity', 'proven track record', 'results-driven', 'detail-oriented', 'self-starter', 'think outside the box',
           'wealth of experience', 'deeply resonates', 'resonates with me', 'aligns perfectly', 'i am writing to express')
SALUTATION = re.compile(r'^\s*(?:dear\b[^\n]{0,80}|to whom it may concern|hello[^\n]{0,40}|hi[^\n]{0,40}|greetings[^\n]{0,40})[,:]?\s*$', re.I)
SIGNOFF = re.compile(r'^\s*(?:sincerely|best regards|kind regards|warm regards|regards|best|thank you|thanks|respectfully|cheers|yours truly|yours sincerely)[,.!]?\s*$', re.I)
DEFAULT_PREFERENCES = {'avoid_phrases': [], 'exclamations': 'match', 'contractions': 'match', 'max_sentence_words': 0, 'notes': ''}


def _sentences(text):
    return [part.strip() for part in re.split(r'(?<=[.!?])\s+(?=[A-Z"“(])', ' '.join(text.split())) if len(part.split()) >= 2]


def _samples(store):
    """Approved writing in the applicant's own voice. Reference material is someone else's wording."""
    return [dict(row) for row in store.db.execute(
        "SELECT id,revision,kind,role,text FROM materials WHERE confirmed=1 AND (role='style' OR kind IN ('cover_letter','writing_sample')) "
        "AND role!='reference' ORDER BY id")]


def validate_preferences(value):
    if not isinstance(value, dict) or set(value) - set(DEFAULT_PREFERENCES):
        raise ValueError('Unknown writing preference')
    result = {**DEFAULT_PREFERENCES, **value}
    phrases = result['avoid_phrases']
    if (not isinstance(phrases, list) or len(phrases) > 40
            or any(not isinstance(item, str) or not 2 <= len(item.strip()) <= 80 or '\n' in item for item in phrases)):
        raise ValueError('List up to 40 short phrases to avoid')
    result['avoid_phrases'] = list(dict.fromkeys(' '.join(item.split()) for item in phrases))
    if result['exclamations'] not in ('match', 'never') or result['contractions'] not in ('match', 'avoid', 'use'):
        raise ValueError('Choose how to handle exclamation marks and contractions')
    limit = result['max_sentence_words']
    if type(limit) is not int or not (limit == 0 or 12 <= limit <= 60):
        raise ValueError('Longest sentence must be 12–60 words, or 0 for no limit')
    if not isinstance(result['notes'], str) or len(result['notes']) > 600:
        raise ValueError('Keep writing notes under 600 characters')
    result['notes'] = result['notes'].strip()
    return result


def preferences(store):
    row = store.db.execute('SELECT value,revision FROM voice_preferences WHERE id=1').fetchone()
    return (validate_preferences(json.loads(row['value'])), row['revision']) if row else (dict(DEFAULT_PREFERENCES), 0)


def save_preferences(store, value, revision):
    value = validate_preferences(value)
    with store.transaction():
        current = preferences(store)[1]
        if type(revision) is not int or revision != current:
            raise ValueError('Writing preferences changed elsewhere. Reload before saving.')
        store.db.execute('INSERT OR REPLACE INTO voice_preferences VALUES(1,?,?,?)', (json.dumps(value), current + 1, now()))
        # Earlier drafts were written under different instructions.
        removed = store.discard_prepared()
        store.event('voice_preferences_saved', 'voice', {'revision': current + 1, 'drafts_removed': removed})
    return {'preferences': value, 'revision': current + 1, 'drafts_removed': removed}


def profile(store):
    """Measurements with plain descriptions, the samples they came from, and saved preferences."""
    samples = _samples(store)
    saved, revision = preferences(store)
    text = '\n\n'.join(sample['text'] for sample in samples)
    sentences = _sentences(text)
    words = re.findall(r"[A-Za-z][A-Za-z'’-]*", text)
    result = {'sample_count': len(samples), 'word_count': len(words),
              'sources': [{'id': sample['id'], 'revision': sample['revision'], 'kind': sample['kind'], 'role': sample['role'],
                           'words': len(sample['text'].split())} for sample in samples],
              'preferences': saved, 'preferences_revision': revision, 'measured': None, 'summary': []}
    if len(sentences) < 5 or len(words) < 80:
        result['summary'] = ['Not enough approved writing to describe your voice yet. Add a writing sample or a past cover letter under Documents & writing. '
                             'Until then, drafts use plain, direct wording.']
        return result
    lengths = sorted(len(sentence.split()) for sentence in sentences)
    paragraphs = [part for part in re.split(r'\n\s*\n', text) if len(part.split()) >= 8]
    per_paragraph = [len(_sentences(part)) or 1 for part in paragraphs]
    count = len(sentences)
    rate = lambda pattern: round(100 * len(re.findall(pattern, text)) / count, 1)
    first_person = sum(1 for sentence in sentences if re.match(r"(?:I|I'm|I’ve|I've|I’m|My|We|Our)\b", sentence))
    contractions = len(re.findall(r"\b\w+(?:n't|n’t|'re|’re|'ve|’ve|'ll|’ll|'m|’m|'d|’d)\b", text))
    openings = {}
    for sentence in sentences:
        first = sentence.split()[0].strip('"“(').casefold()
        openings[first] = openings.get(first, 0) + 1
    lowered = text.casefold()
    measured = {'median_sentence_words': int(median(lengths)), 'short_sentence_share': round(sum(n <= 10 for n in lengths) / count, 2),
                'long_sentence_share': round(sum(n >= 28 for n in lengths) / count, 2), 'longest_sentence_words': lengths[-1],
                'median_paragraph_sentences': int(median(per_paragraph)) if per_paragraph else 0,
                'first_person_opening_share': round(first_person / count, 2),
                'contractions_per_100_words': round(100 * contractions / len(words), 1),
                'exclamations_per_100_sentences': rate(r'!'), 'questions_per_100_sentences': rate(r'\?'),
                'dashes_per_100_sentences': rate(r'—|–| - '), 'semicolons_per_100_sentences': rate(r';'),
                'parentheses_per_100_sentences': rate(r'\('), 'average_word_letters': round(sum(map(len, words)) / len(words), 1),
                'common_openings': [word for word, _ in sorted(openings.items(), key=lambda item: (-item[1], item[0]))[:5]],
                'stock_phrases_used': sorted(phrase for phrase in GENERIC if phrase in lowered)}
    result['measured'] = measured
    middle = measured['median_sentence_words']
    summary = [f'Sentences run about {middle} words; ' + (
        'you mix in short ones often.' if measured['short_sentence_share'] >= .3 else
        'you rarely write very short ones.' if measured['short_sentence_share'] <= .1 else 'with some short ones for emphasis.')]
    summary.append(f'Paragraphs are about {measured["median_paragraph_sentences"]} sentence{"s" if measured["median_paragraph_sentences"] != 1 else ""} long.')
    summary.append('You usually open sentences in the first person.' if measured['first_person_opening_share'] >= .4 else
                   'You vary sentence openings and do not lead with “I” much.')
    summary.append('You use contractions.' if measured['contractions_per_100_words'] >= .8 else 'You mostly avoid contractions.')
    marks = [name for name, key in (('dashes', 'dashes_per_100_sentences'), ('semicolons', 'semicolons_per_100_sentences'),
                                    ('parentheses', 'parentheses_per_100_sentences')) if measured[key] >= 8]
    summary.append('Punctuation: ' + (', '.join(marks) + ' appear regularly' if marks else 'mostly commas and full stops')
                   + ('; no exclamation marks.' if measured['exclamations_per_100_sentences'] == 0 else '; occasional exclamation marks.'))
    summary.append('Vocabulary is plain and concrete.' if measured['average_word_letters'] <= 4.8 else 'Vocabulary leans formal or technical.')
    result['summary'] = summary
    return result


def guidance(store):
    """The compact, text-free description given to the drafting model."""
    described = profile(store)
    saved = described['preferences']
    measured = described['measured'] or {}
    exclamations = saved['exclamations'] == 'match' and measured.get('exclamations_per_100_sentences', 0) > 0
    contractions = (saved['contractions'] == 'use' or
                    saved['contractions'] == 'match' and measured.get('contractions_per_100_words', 0) >= .8)
    allowed_stock = set(measured.get('stock_phrases_used', []))
    return {'description': described['summary'], 'median_sentence_words': measured.get('median_sentence_words'),
            'typical_paragraph_sentences': measured.get('median_paragraph_sentences'),
            'use_contractions': contractions, 'allow_exclamation_marks': exclamations,
            'longest_sentence_words': saved['max_sentence_words'] or None,
            'avoid_phrases': saved['avoid_phrases'], 'avoid_stock_phrases': sorted(set(GENERIC) - allowed_stock)[:40],
            'applicant_notes': saved['notes']}


def fingerprint(store):
    """Changes when saved preferences change; None until the applicant saves any."""
    saved, revision = preferences(store)
    return digest([revision, saved]) if revision else None


def strip_letter_frame(text, question=''):
    """Remove a greeting or sign-off from a field that asked for a paragraph. Deletes lines only."""
    if re.search(r'\bcover letter\b', question, re.I) and not re.search(r'omit greetings|body of a cover letter', question, re.I):
        return text
    lines = text.strip().split('\n')
    while lines and (SALUTATION.match(lines[0]) or not lines[0].strip()):
        lines.pop(0)
    # A sign-off, then optionally a short name line beneath it.
    for index in range(len(lines) - 1, max(-1, len(lines) - 4), -1):
        if SIGNOFF.match(lines[index]) and all(len(line.split()) <= 4 for line in lines[index + 1:]):
            lines = lines[:index]
            break
    while lines and not lines[-1].strip():
        lines.pop()
    return '\n'.join(lines)


def review(text, guide, *, posting='', previous=()):
    """Deterministic voice notes for one repair pass. Notes never block an answer."""
    notes = []
    lowered = ' '.join(text.casefold().split())
    for phrase in guide.get('avoid_phrases', []):
        if phrase.casefold() in lowered:
            notes.append(f'Remove “{phrase}”: the applicant asked not to use it.')
    stock = [phrase for phrase in guide.get('avoid_stock_phrases', []) if phrase in lowered]
    if stock:
        notes.append('Replace stock phrases with a concrete statement: ' + ', '.join(f'“{phrase}”' for phrase in stock[:5]) + '.')
    if '!' in text and not guide.get('allow_exclamation_marks'):
        notes.append('Remove exclamation marks; the applicant does not use them.')
    if not guide.get('use_contractions') and re.search(r"\b\w+(?:n't|n’t|'re|’re|'ve|’ve|'ll|’ll|'m|’m)\b", text):
        notes.append('Write contractions out in full to match the applicant.')
    sentences = _sentences(text)
    limit = guide.get('longest_sentence_words')
    if limit and any(len(sentence.split()) > limit for sentence in sentences):
        notes.append(f'Split sentences longer than {limit} words.')
    starts = [sentence.split()[0].casefold() for sentence in sentences]
    if any(starts[i] == starts[i + 1] == starts[i + 2] == 'i' for i in range(len(starts) - 2)):
        notes.append('Three sentences in a row start with “I”; vary the openings.')
    words = lowered.split()
    source = ' '.join(posting.casefold().split())
    if source and any(' '.join(words[i:i + 9]) in source for i in range(max(0, len(words) - 8))):
        notes.append('A passage repeats the job description word for word; say it in the applicant\'s own terms.')
    earlier = {' '.join(sentence.casefold().split()) for item in previous for sentence in _sentences(item) if len(sentence.split()) >= 7}
    if any(' '.join(sentence.casefold().split()) in earlier for sentence in sentences):
        notes.append('A sentence repeats an earlier answer in this application; write it fresh for this question.')
    return notes
