"""Small native-widget operations shared by supported ATS flows."""
from .util import Blocked

LEVER_LOOKUP_TIMEOUT=5000


def lever_location_value(control):
    """Lever clears unselected text on blur; require its selected object too."""
    import json
    selected=control.locator('xpath=..').locator('input[type=hidden][name=selectedLocation]')
    if selected.count()!=1:return ''
    try:value=json.loads(selected.input_value())
    except (ValueError,TypeError):return ''
    name=value.get('name') if isinstance(value,dict) else None
    return name if isinstance(name,str) and name and name==control.input_value() else ''


def _lever_location_search(browser,control,query):
    # The official widget searches on keydown, not Playwright's fill/input event.
    # Clear stale suggestions before a new lookup can select an old row index.
    options=control.locator('xpath=..').locator('.dropdown-results .dropdown-location')
    control.fill('');control.press('Backspace')
    options.first.wait_for(state='detached',timeout=3000)
    control.press_sequentially(query,delay=10)
    return options


def lever_location_snapshot(browser,control):
    from playwright.sync_api import TimeoutError
    selected=lever_location_value(control)
    if selected:return {'options':[selected],'value':selected}
    fact=browser.store.facts().get('location')
    if not fact:return {'options':[],'value':control.input_value()}
    found=[]
    try:
        for query in dict.fromkeys((fact['value'],fact['value'].split(',')[0])):
            if not 0<len(query)<=100:continue
            browser.store.checkpoint()
            options=_lever_location_search(browser,control,query)
            try:options.first.wait_for(state='visible',timeout=LEVER_LOOKUP_TIMEOUT)
            except TimeoutError:continue
            found.extend(options.evaluate_all('(nodes)=>nodes.filter(n=>n.getClientRects().length).map(n=>n.textContent.trim()).filter(Boolean)'))
    except TimeoutError:raise Blocked('unsupported_widget','Current location suggestions could not be reset') from None
    finally:
        control.fill('');control.press('Tab')
    return {'options':sorted(set(found)),'value':''}


def select_lever_location(browser,control,field,label):
    import re
    from playwright.sync_api import TimeoutError
    if lever_location_value(control)==label:return
    try:
        options=_lever_location_search(browser,control,label.split(',')[0])
        target=options.filter(has_text=re.compile('^'+re.escape(label)+'$'))
        target.first.wait_for(state='visible',timeout=LEVER_LOOKUP_TIMEOUT)
        if target.count()!=1:raise Blocked('option_mismatch',field['label'])
        target.click();control.press('Tab')
    except TimeoutError:raise Blocked('unsupported_widget','Current location choice did not become selectable') from None
    if lever_location_value(control)!=label:raise Blocked('field_verification_failed',field['label'])

def committed_chips(control):
    return control.evaluate("""e=>{
        for(let n=e.parentElement,depth=0;n&&depth<5;n=n.parentElement,depth++){
            if(n.querySelectorAll('[role=combobox]').length>1)return [];
            const labels=Array.from(n.querySelectorAll('[class*=multi-value__label],[class*=multiValueLabel]'))
                .filter(x=>x.getClientRects().length).map(x=>x.textContent.trim()).filter(Boolean);
            if(labels.length)return labels;
        }return [];}""")

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
    if field.get('lever_location'):return select_lever_location(browser,control,field,label)
    if committed_chips(control)==[label]:return
    from playwright.sync_api import TimeoutError
    from .answers import field_key
    import re
    def exact_target():
        menu=browser._menu(control)
        plain=menu.get_by_role('option',name=label,exact=True)
        if field_key(field['label'])=='school':
            canonical=menu.get_by_role('option').filter(has=browser.page.locator(
                '[class*=canonicalSchoolResultName]',has_text=re.compile(r'^'+re.escape(label)+r'$')))
            return canonical.or_(plain)
        return plain
    browser._open_combobox(control)
    target=exact_target()
    if target.count()>1:raise Blocked('option_mismatch',field['label'])
    # Existing visible choices are authoritative. Typing an abbreviation can
    # erase a static menu even when its meaning was mapped correctly.
    if target.count()!=1 and control.evaluate('(e)=>e.tagName==="INPUT"'):
        control.fill(label.split(',')[0] if field_key(field['label'])=='location' else label)
        target=exact_target()
    try:
        target.first.wait_for(state='visible',timeout=5000)
        if target.count()!=1:raise Blocked('option_mismatch',field['label'])
        target.click(timeout=5000)
    except TimeoutError:raise Blocked('unsupported_widget',field['label']+' — validated option did not become selectable') from None
