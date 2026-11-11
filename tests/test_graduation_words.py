import pytest

from hireme.graduation import required, window
from hireme.util import Blocked


@pytest.mark.parametrize('text',[
    'You must be a current undergraduate at a US or Canadian university.',
    'An undergraduate degree is required.',
    'Undergraduates must be available for the entire program.',
])
def test_undergraduate_requirement_is_not_a_graduation_date_cutoff(text):
    required(text, '2028-05')
    assert window(text) is None


def test_real_graduation_cutoff_after_undergraduate_requirement_still_applies():
    text='You must be a current undergraduate. You must graduate before May 2028.'
    with pytest.raises(Blocked, match='graduation_mismatch'):
        required(text, '2028-05')
    required(text, '2028-04')


def test_unparsed_actual_graduation_requirement_stays_held():
    with pytest.raises(Blocked, match='graduation_window_review'):
        required('You must graduate within six months of the internship.', '2028-05')


@pytest.mark.parametrize('date,accepted',[('2027-08',False),('2027-09',True),('2028-05',True),('2028-08',True),('2028-09',False)])
def test_inclusive_season_range(date,accepted):
    text='Expected to graduate between Fall 2027 and Summer 2028, and enrolled throughout the internship'
    if accepted:required(text,date)
    else:
        with pytest.raises(Blocked,match='graduation_mismatch'):required(text,date)


def test_discrete_seasons_do_not_admit_the_gap():
    with pytest.raises(Blocked,match='graduation_mismatch'):
        required('Must graduate either Fall 2027 or Summer 2028.','2028-05')
