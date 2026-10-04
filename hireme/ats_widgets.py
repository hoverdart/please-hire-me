"""Small native-widget operations shared by supported ATS flows."""
from .util import Blocked

def select_exact(control, field, label):
    options=field.get('options',[])
    if options.count(label)!=1:raise Blocked('option_mismatch',field['label'])
    values=field.get('option_values')
    if values is not None:
        if len(values)!=len(options):raise Blocked('invalid_option_metadata',field['label'])
        control.select_option(value=values[options.index(label)])
    else:control.select_option(label=label)
    if control.locator('option:checked').inner_text().strip()!=label:
        raise Blocked('field_verification_failed',field['label'])


def select_combobox_exact(browser,control,field,label):
    if field.get('options',[]).count(label)!=1:raise Blocked('option_mismatch',field['label'])
    from playwright.sync_api import TimeoutError
    from .answers import field_key
    control.click()
    target=browser._menu(control).get_by_role('option',name=label,exact=True)
    if target.count()>1:raise Blocked('option_mismatch',field['label'])
    # Existing visible choices are authoritative. Typing an abbreviation can
    # erase a static menu even when its meaning was mapped correctly.
    if target.count()!=1 and control.evaluate('(e)=>e.tagName==="INPUT"'):
        control.fill(label.split(',')[0] if field_key(field['label'])=='location' else label)
        target=browser._menu(control).get_by_role('option',name=label,exact=True)
    try:target.click(timeout=5000)
    except TimeoutError:raise Blocked('unsupported_widget',field['label']+' — validated option did not become selectable') from None
