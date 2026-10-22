"""An omitted interviewer pronoun must not hide the next-topic lead-in."""
import json
from pathlib import Path
import sys
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'linyuan'))
import source_selection as S


@pytest.mark.parametrize('tail', [
    '嗯，那接下来聊聊白酒吧。', '好的，下面谈一下银行。',
    '那么，接着讨论孩子教育。', '那接下来聊聊企业招聘。',
    '嗯，那我们接下来聊聊日常饮食。',
])
def test_next_topic_without_a_subject_cannot_end_the_previous_answer(tail):
    cues = [dict(start=0,end=5,text='您怎么看这个行业？'),
            dict(start=5,end=35,text='我们看到需求在增长，也要考虑价格和风险。'),
            dict(start=35,end=39,text=tail)]
    assert '换题' in S.boundary_error(cues,dict(start=0,end=2))
    assert S.boundary_error(cues,dict(start=0,end=1)) is None


@pytest.mark.parametrize('text', [
    '接下来聊聊这个问题。', '下面谈一下刚才的问题。',
    '接着讨论同一个行业。', '接下来布局大健康。',
    '我的朋友说接下来聊聊银行，我没有回答。',
])
def test_same_topic_action_and_reported_speech_remain_allowed(text):
    assert not S.TOPIC_CHANGE.search(text)


def test_actual_published_tail_reselects_at_original_boundary(monkeypatch):
    case=json.loads((Path(__file__).parent/'fixtures/linyuan_published_topic_tail.json').read_text())
    cues=case['cues'];before=json.dumps(cues,ensure_ascii=False)
    monkeypatch.setattr(S.editorial,'CONTENT_POLICY','reference_v1')
    monkeypatch.setattr(S.editorial,'MIN_SECONDS',20)
    assert '换题' in S.boundary_error(cues,dict(start=0,end=len(cues)-1))
    picks=S.select(cues,limit=None,whole_source=True)
    assert picks
    assert all(p['end']<len(cues)-1 for p in picks)
    assert all(S.boundary_error(cues,p) is None for p in picks)
    assert json.dumps(cues,ensure_ascii=False)==before
