import json
from pathlib import Path
import sys
import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'linyuan'))
import title_rewrite as T
from curated_editorial import source_ranges


@pytest.mark.parametrize('ident,title',[(7,'林园：不挣钱，但是值得投资'),(79,'林园：我这个人最喜欢危机')])
def test_selected_original_quote_matches_actual_complete_passage(ident,title):
    cases=json.loads((ROOT/'linyuan/simulations/benchmark-20260921/all-current-title-corpus.json').read_text())
    case=next(r for r in cases if r['source_id']==ident)
    ranges=source_ranges(case['cues'],case['source_sha256'])
    a,b,picks=ranges[0]
    assert (a,b)==(0,len(case['cues'])-1)
    assert picks[0]['editorial_title']==title and picks[0]['editorial_prefer_exact_quote']
    source=''.join(c['text'] for c in case['cues'])
    result=T._extractive(source,'林园',[],preferred=title,guest_passages=[source],only_preferred=True)
    assert result['title']==title and T.error(title,result['title_rewrite'],source) is None


def test_actual_crisis_observation_cannot_be_reversed_in_either_copy_field():
    source='这就是危机。我们从投资的角度上我们倾向于买危机。我这个人最喜欢危机。'
    assert T.relation_error('医药行业不是危机，是机会','我喜欢危机',source)
    assert T.relation_error('我喜欢危机','不是危机，是机会',source)
    assert T.relation_error('我这个人最喜欢危机','我喜欢危机',source) is None
    assert T.relation_error('不是危机','不是危机','这不是危机。') is None


def test_personal_predicate_requires_a_complete_subject_and_object():
    from headline_policy import complete
    assert complete('我这个人最喜欢危机')
    assert complete('我们拒绝诱惑')
    for fragment in ('我喜欢','我喜欢的人','喜欢危机','我喜欢这些','我喜欢什么'):
        assert not complete(fragment)


def test_actual_old_model_approval_cannot_bypass_crisis_meaning_check():
    case=json.loads((ROOT/'tests/fixtures/linyuan_crisis_title_regression.json').read_text())
    assert '不能为制造反差' in T.error(case['title'],case['proof'],case['transcript'])
