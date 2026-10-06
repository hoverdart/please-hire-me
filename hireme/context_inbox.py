"""Free-form applicant notes, organized by a model and saved only after the applicant reviews them."""
from __future__ import annotations

from .config import FACTS, validate_fact
from .util import Blocked

MAX_NOTES = 6000
# Retryable model outcomes and form defects are not information the applicant can supply.
NOT_NEEDS = ('provider_timeout', 'provider_error', 'provider_invalid_output', 'writing_unsupported',
             'model_budget_exhausted', 'unknown_prefilled_value', 'unsupported_widget', 'email_verification_required')


def _plain(text):
    return ' '.join(str(text).casefold().split())


def open_needs(store, limit=12):
    """Questions that recur across held applications, most common first."""
    from .presentation import actionable_question_sql
    condition, parameters = actionable_question_sql('q')
    rows = store.db.execute(
        f"SELECT q.label,count(DISTINCT q.job_id) AS jobs FROM questions q WHERE {condition} "
        f"AND q.reason NOT IN ({','.join('?' for _ in NOT_NEEDS)}) GROUP BY q.label "
        "ORDER BY jobs DESC,max(q.rowid) DESC LIMIT ?", (*parameters, *NOT_NEEDS, limit))
    return [{'label': ' '.join(r['label'].split()).rstrip(' *'), 'jobs': r['jobs']} for r in rows]


def organize(store, text, provider):
    """Propose fact updates and context notes. Nothing is saved here."""
    if not isinstance(text, str) or not 3 <= len(text.strip()) <= MAX_NOTES:
        raise ValueError(f'Write 3–{MAX_NOTES:,} characters')
    needs = open_needs(store, 30)
    current = {k: v['value'] for k, v in store.facts().items()}
    draft = provider.organize_context(text, FACTS, current, [n['label'] for n in needs])
    source = _plain(text)
    grounded = lambda quote: isinstance(quote, str) and quote.strip() and _plain(quote) in source
    facts, notes, skipped = [], [], []
    for item in draft.get('facts', []):
        key = item.get('key')
        if key not in FACTS or not grounded(item.get('quote')):
            continue
        try:
            value = validate_fact(key, item.get('value', ''))
        except ValueError as error:
            skipped.append(f"{FACTS[key]}: {error}")
            continue
        if current.get(key) != value:
            facts.append({'key': key, 'label': FACTS[key], 'value': value, 'current': current.get(key, '')})
    for item in draft.get('notes', []):
        topic = ' '.join(str(item.get('topic', '')).split()).strip(' :')
        statement = ' '.join(str(item.get('statement', '')).split())
        if not topic or not statement or not grounded(item.get('quote')):
            continue
        line = f"{topic}: {statement}" + ('' if statement.endswith(('.', '!', '?')) else '.')
        if line not in notes:
            notes.append(line)
    covers = sorted({needs[i]['label'] for i in draft.get('covers', []) if isinstance(i, int) and 0 <= i < len(needs)})
    unclear = [' '.join(str(x).split()) for x in draft.get('unclear', []) if str(x).strip()][:8]
    return {'facts': facts, 'notes': notes, 'unclear': unclear + skipped, 'covers': covers}


def apply(store, facts, notes, revision, confirmed):
    """Save reviewed facts and append reviewed notes to Basic context."""
    from .materials import basic_context, save_basic_context
    if confirmed is not True:
        raise ValueError('Confirm that you reviewed these before saving')
    if not isinstance(facts, dict) or not isinstance(notes, str):
        raise ValueError('Provide reviewed facts and notes')
    values = {key: validate_fact(key, value) for key, value in facts.items()}
    lines = [' '.join(line.split()) for line in notes.splitlines() if line.strip()]
    if not values and not lines:
        raise ValueError('Nothing to save')
    context = basic_context(store)
    if type(revision) is not int or revision != context['revision']:
        raise ValueError('Basic context changed elsewhere. Reload before saving.')
    existing = context['text'].splitlines() if context['text'] else []
    added = [line for line in lines if line not in {' '.join(x.split()) for x in existing}]
    text = '\n'.join([*existing, *added]).strip()
    if added and not 20 <= len(text) <= 12000:
        raise ValueError('Basic context must stay within 20–12,000 characters; shorten the notes')
    with store.transaction():
        if values:
            store.put_facts(values)
        if added:
            save_basic_context(store, text, revision)
    store.export_config()
    return {'facts': sorted(values), 'notes': len(added)}


def organize_with_model(store, text):
    from .provider import LazyProvider
    provider = LazyProvider(store.settings()['model_timeout_seconds'], store=store)
    try:
        return organize(store, text, provider)
    except Blocked as error:
        raise ValueError({'provider_timeout': 'The model did not answer in time. Try a shorter note or raise the model timeout.',
                          'model_budget_exhausted': 'Today’s model request limit is used up. Try again tomorrow or raise the limit.'}
                         .get(error.reason, f'The model could not organize this ({error.reason}). Check Model connection.')) from None
