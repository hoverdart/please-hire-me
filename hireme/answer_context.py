"""Country references must follow the validated employer country selection."""
import re


GENERIC_WORK_COUNTRY = re.compile(
    r'\b(?:the )?country (?:where|for which|in which|of (?:the )?(?:role|position|employment))\b|\b(?:this|that) country\b', re.I)


def employment_country_question(label):
    return bool(re.search(r'employment eligible countries.*(?:seeking|work)|'
                          r'(?:which|what) country.*(?:seeking|wish|intend|would like).*work', label, re.I))


def us_city_location(location):
    """Exact distinctive US city labels; mixed or unknown locations fail."""
    places=[p.strip().casefold() for p in re.split(r'[,;/]',location)]
    return bool(places) and all(p in {'new york', 'new york city', 'san francisco', 'seattle', 'chicago'} for p in places)


def selected_employment_country(context):
    selections = [a['value'] for a in context.get('previous_answers', [])
                  if employment_country_question(a.get('field', {}).get('label', ''))]
    return selections[-1] if selections else None


def work_country_location(context):
    selection=selected_employment_country(context)
    if selection:return selection
    countries=context.get('employment_countries')
    if countries:
        return 'United States' if all(x.casefold() in {'united states','us','usa','united states of america'} for x in countries) else ''
    return context.get('location', '')
