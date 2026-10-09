"""Real source: retain independent guest review after a complete question."""
from pathlib import Path
import sys
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan'))
import title_rewrite as T

UNITS=[
    '那在这一轮呢，整个市场启动之后，',
    '您觉得这一轮跟前面几轮呢，嗯，',
    '股市牛市的这个演绎会有什么不同呢？',
    '尽管这过程中还会有，因为牛市初期还会',
    '有反复，但是我们相信它趋势已经形成',
    '了。',
    '好的，好的，好的。',
    '那看来林总给了一个关键词啊，',
    '给大家一个关键词：趋势啊，相信趋势的',
    '力量。一波牛市的趋势已经确立之后，',
    '可能往上的空间和力量都是非常非常大、',
    '非常非常强的啊。',
]


def test_actual_guest_span_is_no_longer_excluded_but_host_recap_stays_blocked():
    excluded=T.explicit_host_cues(UNITS)
    assert not excluded.intersection({3,4,5})
    assert {1,2,7,8,9,10,11}<=excluded
    roles=T.bind_reading(dict(a_guest_answer='尽管牛市初期有反复，但是我们相信趋势已经形成。',
        b_question_premise='主持人询问本轮牛市演绎与以前有何不同。',
        c_guest_spans=[dict(a_start=3,b_end=5)]),UNITS)
    assert T.guest_evidence_ids(UNITS,roles)==[3,4]
    assert T.guest_evidence_ids(UNITS,['host']*len(UNITS))==[]


@pytest.mark.parametrize('answer',[
    '尽管牛市还会反复，但是我们相信趋势形成了吗？',
    '虽然牛市有反复，但是我们相信，您是否赞同。',
    '尽管医药有反复，但是我们相信投资趋势已经形成。',
    '虽然牛市有反复，但是我们相信趋势已经形成',
])
def test_host_followup_unrelated_topic_or_incomplete_statement_still_excluded(answer):
    assert 1 in T.explicit_host_cues(['您觉得牛市会有什么不同？',answer])


def test_no_completed_question_cannot_release_a_host_premise():
    units=['您觉得牛市有什么不同，','尽管牛市会反复，但是我们相信趋势形成了。']
    assert 1 in T.explicit_host_cues(units)
