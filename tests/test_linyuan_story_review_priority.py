"""Review order may change; stories, source text and budget rules may not."""
import copy
from pathlib import Path
import sys

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan'))
import story_review_priority as S
import publishing_experiment as E


def candidate(text):return [dict(text=text)],dict(start=0,end=0)


@pytest.mark.parametrize('text',[
    '我半夜睡不着觉，我就听他们讲笑话。',
    '我们当年买了这家公司，后来拿了很久。',
    '我以前工作时遇到过这样的问题。',
])
def test_concrete_first_person_scene_is_only_a_review_hint(text):
    cues,pick=candidate(text)
    assert S.concrete_scene(cues,pick)
    assert 'story_selection' not in pick


@pytest.mark.parametrize('text',[
    '未来如果我买了这家公司，我就会持有。',
    '如果我当年买了这家公司，现在会有很多钱。',
    '您当年为什么买了这家公司，我想听一听？',
    '我最近认为长期投资很重要。',
    '我们要看未来行业的变化。',
    '公司去年增长很快，行业空间也很大。',
])
def test_hypothetical_host_question_or_generic_opinion_does_not_get_scene_priority(text):
    cues,pick=candidate(text)
    assert not S.concrete_scene(cues,pick)


def test_ranking_is_stable_complete_and_does_not_mutate_candidates_or_cues():
    cues=[dict(text=s) for s in ['长期投资很重要。','我半夜睡不着就听笑话。',
                                '行业趋势仍然向好。','我当年买了这家公司。']]
    picks=[dict(start=i,end=i,original=i) for i in range(4)]
    before=copy.deepcopy((cues,picks))
    ranked=S.rank_candidates(cues,picks)
    assert [p['original'] for p in ranked]==[1,3,0,2]
    assert (cues,picks)==before
    assert {id(p) for p in ranked}=={id(p) for p in picks}
    assert E.SELECTION_REVISION==2026102201 and E.VERSION==2026101801


def test_priority_never_makes_hypothetical_evidence_a_valid_story():
    cues,pick=candidate('如果我当年买了这家公司，就会赚很多钱。长期持有很重要。')
    assert S.rank_candidates(cues,[pick])==[pick]
    with pytest.raises(ValueError):
        E.validate_choices(cues,[pick],dict(choices=[dict(index=0,kind='personal_story',
            subject_quote='这家公司',event_quote='如果我当年买了这家公司',detail_quote='长期持有很重要')]))


@pytest.mark.parametrize('event',[
    '如果我当年买了这家公司，就会赚很多钱。',
    '比如说，假如我们买了茅台，就会有回报。',
    '要是当时我卖了股票，就不用亏钱。',
])
def test_counterfactual_self_action_is_not_a_past_event(event):
    assert E.personal_event_error(event)


@pytest.mark.parametrize('event',[
    '我当年买了这家公司，后来一直持有。',
    '我们当时提出了一个理论是消费升级。',
    '我当时买了股票，如果没买就不会有这些回报。',
])
def test_real_actions_and_the_existing_accepted_company_reason_remain_valid(event):
    assert E.personal_event_error(event) is None
