"""Complete exact guest-source words; never bridge speakers or invent facts."""
from pathlib import Path
import sys

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan'))
import title_rewrite as T


def source():return ['从二零零零年以后，','我基本上没有卖出。','比如说我们的白','酒，我可能是终身持有，不会卖。']


def test_catalog_maps_the_whole_word_to_both_original_cue_ids():
    units=source()
    assert T.subject_catalog(units,allowed_ids=[0,1,2,3])['白酒']==[2,3]
    assert '白酒' not in T.subject_catalog(units,allowed_ids=[0,1,3])
    assert T.subject_cue_spans('白酒',units)==[[2,3]]


def test_only_the_missing_half_of_a_trusted_guest_word_is_completed():
    units=source();catalog=T.subject_catalog(units,allowed_ids=[0,1,2,3])
    item=dict(title='林园：白酒可能终身持有，我基本上不卖',cover_title='白酒可能终身持有')
    focus=dict(a_claim='二零零零年后基本不卖，白酒可能终身持有',b_evidence_ids=[0,1,3])
    bound=T.bind_guest_candidate(item,focus,units,catalog,[0,1,2,3])
    assert bound['subject']=='白酒' and bound['evidence_ids']==[0,1,2,3]
    assert bound['title']==item['title'] and bound['cover_title']==item['cover_title']
    assert bound['evidence']==[units[0],units[1],units[2]+units[3]]
    assert bound['source_word_boundary_completion']['raw_evidence_ids']==[0,1,3]
    assert all(quote in ''.join(units) for quote in bound['evidence'])


@pytest.mark.parametrize('guest_ids',[[0,1,3],[0,1,2]])
def test_host_or_unknown_half_cannot_become_guest_source(guest_ids):
    units=source();catalog=T.subject_catalog(units,allowed_ids=guest_ids)
    assert '白酒' not in catalog
    ids=[i for i in [0,1,3] if i in guest_ids]
    bound=T.bind_guest_candidate(dict(title='林园：白酒可能终身持有',cover_title='白酒可能终身持有'),
        dict(a_claim='本人明确说出的实际选择',b_evidence_ids=ids),units,catalog,guest_ids)
    assert bound['subject']!='白酒' and 'source_word_boundary_completion' not in bound


def test_no_extra_fact_is_selected_when_neither_half_was_chosen():
    units=source();catalog=T.subject_catalog(units,allowed_ids=[0,1,2,3])
    bound=T.bind_guest_candidate(dict(title='林园：白酒可能终身持有',cover_title='白酒可能终身持有'),
        dict(a_claim='本人明确说出的实际选择',b_evidence_ids=[0,1]),units,catalog,[0,1,2,3])
    assert not bound['subject'] and bound['evidence_ids']==[0,1]


def test_four_id_budget_and_nonadjacent_or_punctuated_words_are_not_bypassed():
    units=['原话甲','原话乙','原话丙','原话丁','我们的白','酒可能终身持有。']
    catalog=T.subject_catalog(units,allowed_ids=list(range(6)))
    bound=T.bind_guest_candidate(dict(title='林园：白酒可能终身持有',cover_title='白酒可能终身持有'),
        dict(a_claim='本人明确说出的实际选择',b_evidence_ids=[0,1,2,5]),units,catalog,list(range(6)))
    assert not bound['subject'] and bound['evidence_ids']==[0,1,2,5]
    assert not T.subject_cue_spans('白酒',['我们的白。','酒可能终身持有。'])
    assert not T.subject_cue_spans('白酒',['我们的白','换了一个话题','酒可能终身持有。'])


def test_already_bound_whole_word_uses_unchanged_evidence():
    units=['我过去买过白酒。','后来我很少卖出。'];catalog=T.subject_catalog(units)
    bound=T.bind_guest_candidate(dict(title='林园：买过白酒后我很少卖出',cover_title='买过白酒后很少卖出'),
        dict(a_claim='买过白酒后很少卖',b_evidence_ids=[0,1]),units,catalog,[0,1])
    assert bound['evidence']==units and 'source_word_boundary_completion' not in bound
