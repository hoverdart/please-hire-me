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
