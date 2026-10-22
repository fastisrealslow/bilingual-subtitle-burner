from pathlib import Path
import sys

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan'))
import title_rewrite as T
import publishing_experiment as E

SOURCE='就刚才我举的那个例子，比如说我们的白酒，我可能是终身持有，不会卖。'


@pytest.mark.parametrize('title,cover',[
    ('林园：二零零零年后基本不再卖出，白酒更是终身持有不卖','二零零零年后基本不再卖出'),
    ('林园：白酒可能终身持有','白酒终身持有不卖'),
    ('林园：白酒我拿一辈子','白酒可能终身持有'),
    ('林园：白酒我打算终身持有','白酒可能终身持有'),
])
def test_actual_review_approved_strengthening_is_rejected_on_each_surface(title,cover):
    assert T.lifetime_holding_hedge_error(title,cover,SOURCE)
    assert T.forecast_copy_error(title,cover,SOURCE)


def test_original_possible_stance_is_kept_without_inventing_new_conditions():
    assert T.forecast_copy_error('林园：白酒可能终身持有，基本很少卖出','白酒可能终身持有',SOURCE) is None
    assert T.lifetime_holding_hedge_error('林园：白酒基本不卖','白酒基本不卖',SOURCE) is None


def test_explicit_unqualified_source_is_not_forced_to_add_uncertainty():
    source='我会终身持有白酒，不会卖。'
    assert T.lifetime_holding_hedge_error('林园：我会终身持有白酒','我会终身持有白酒',source) is None
    assert T.lifetime_holding_hedge_error('林园：白酒永久不卖','白酒永久不卖','我很少卖白酒。')


def test_a_hedge_in_another_sentence_cannot_qualify_the_lifetime_copy():
    assert T.lifetime_holding_hedge_error('林园：可能看好消费。白酒终身持有','白酒可能终身持有',SOURCE)


def test_the_shared_story_title_and_delivery_gate_rejects_the_actual_strengthening():
    selection=dict(version=E.VERSION,kind='personal_story',subject_quote='我这辈子基本很少卖出。',
        event_quote='从二零零零年以后，我基本上没有卖特别卖出的这个行为。',detail_quote=SOURCE,
        transcript_sha256='a'*64)
    title='林园：二零零零年后基本不再卖出，白酒更是终身持有不卖'
    assert '可能' in E.title_focus_error(title,E.title_focus(selection))
    meta=dict(editorial_profile=E.TREATMENT,title=title,experiment_proof=dict(
        version=E.VERSION,profile=E.TREATMENT,source_duration_sec=1800,selection=selection))
    assert '可能' in E.proof_error(meta)
