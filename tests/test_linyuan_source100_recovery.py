"""Real failed transcripts and unchanged media gates for the source100 retry."""
import json
from pathlib import Path
import sys
from unittest.mock import patch
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan'))
import source_selection as selection
import produce_cn as producer
import simulate_sources as sim

FIXTURE=Path(__file__).parent/'fixtures/source100_recovery.json'


def test_real_followups_are_kept_but_the_next_medical_topic_is_excluded():
    cues=json.loads(FIXTURE.read_text())['3']
    picks=selection.select(cues,whole_source=True,limit=None)
    assert any(115<cues[p['start']]['start']<117 and 260<cues[p['end']]['end']<264 for p in picks)
    assert all(not (cues[p['start']]['start']<263 and cues[p['end']]['end']>283) for p in picks)
    for p in picks:
        text=''.join(c['text'] for c in cues[p['start']:p['end']+1])
        assert '百分之一一百的风险' in text
        assert '不一定是你现在投资的公司' in text


@pytest.mark.parametrize('sample',['47','48','76'])
def test_real_complete_speech_is_not_filtered_as_a_disfluent_headline(sample):
    cues=json.loads(FIXTURE.read_text())[sample]
    before=json.dumps(cues,ensure_ascii=False)
    picks=selection.select(cues,whole_source=True)
    assert picks and cues[picks[0]['end']]['end']-cues[picks[0]['start']]['start']>=120
    assert json.dumps(cues,ensure_ascii=False)==before
    assert not selection.select(cues,whole_source=False)


def test_question_topic_change_cannot_be_joined_by_non_technology_word():
    assert not selection.same_topic_followup('您怎么看科技股的风险？','非科技股中，您怎么看医药的机会？')
    assert not selection.same_topic_followup('您怎么看医药？','除了医药您怎么看消费？')
    assert not selection.same_topic_followup('您怎么看企业投资？','您怎么看公司的机会？')


def test_two_subtitle_bands_are_both_removed_without_larger_crop_budget(monkeypatch):
    cov=[0.0]*100;cov[78:81]=[1.0]*3;cov[90:94]=[1.0]*4
    monkeypatch.setattr(producer,'_face_survives',lambda *a:True)
    width,height,x,y=producer.safe_crop_plan(Path('source.mp4'),1920,1080,coverage=cov)
    assert 1080*.7<=height<1080*.78
    cov[65:69]=[1.0]*4
    assert producer.safe_crop_plan(Path('source.mp4'),1920,1080,coverage=cov) is None


def test_visual_value_errors_are_not_reported_as_recoverable_runtime():
    args=dict(finals=[],validation_error='',source={},execution={},steps={})
    reject=dict(stage='part-quality',error_type='ValueError',retryable=False,
                reason='动态取景剩余帧即使全部匹配也达不到80%：已匹配0/830')
    assert sim.classify(batch=dict(rejected=[reject]),**args)==('rejected','candidate-quality')
    reject['reason']='unrecognized codec exception'
    assert sim.classify(batch=dict(rejected=[reject]),**args)[0]=='unresolved'
    assert sim.classify(batch={},**dict(args,source=dict(passed=False,retryable=True,
        reason='RuntimeError: 时长 34s 不在 [120,5400]')))==('rejected','source-quality')


def test_comparison_snapshot_mismatch_is_not_silently_accepted(monkeypatch,tmp_path):
    manifest=dict(comparison=dict(publication_commit='a'*40,publication_sha256='b'*64))
    monkeypatch.setattr(sim,'read',lambda *a:manifest)
    monkeypatch.setattr(sim,'BASE',tmp_path)
    class Response:
        def read(self):return b'wrong snapshot'
    monkeypatch.setattr(sim.urllib.request,'urlopen',lambda *a,**k:Response())
    with pytest.raises(ValueError,match='snapshot hash mismatch'):sim.prepare_snapshot()
    assert not (tmp_path/'_publication_state.json').exists()
def test_topic_cards_without_second_person_cannot_join_unrelated_short_answers():
    import source_selection as selection
    questions=['对股市散户有什么投资建议？','有哪些炒股的书值得推荐？',
               '年轻股民该怎么炒股？','未来中国股市会蓬勃发展吗？']
    cues=[]
    for n,q in enumerate(questions):
        t=n*40
        cues.extend([dict(start=t,end=t+3,text=q),
                     dict(start=t+4,end=t+39,text='企业经营需要控制风险，长期投资必须研究实际经营情况。')])
    assert all(selection.question_unit(q) for q in questions)
    assert selection.select(cues,limit=None,whole_source=True)==[]
    assert not selection.question_unit('比如说，年轻股民该怎么炒股？')


def test_keynote_uses_literal_speaker_boundaries_not_fixed_length_slices():
    cues=[
        dict(start=0,end=110,text='前一部分继续说明当前市场估值与过去周期的差异。'),
        dict(start=111,end=115,text='我再讲一下，这个市场，接下来我们应该投什么？'),
        dict(start=116,end=220,text='我们找到了未来长期增长的行业，接下来布局大健康。'),
        dict(start=221,end=347,text='人口结构决定长期需求，所以重点投三大病预防和并发症治疗药物。'),
        dict(start=348,end=370,text='我指的是药物，一定是药物，因为我本人是学医的。'),
        dict(start=371,end=485,text='医生需要终身学习，我一直留意医药和医疗的新进展。'),
        dict(start=486,end=620,text='三大病的并发症需要长期控制，所以我们只投大健康赛道。'),
    ]
    picks=selection.select(cues,limit=None,whole_source=True)
    assert [(cues[p['start']]['start'],cues[p['end']]['end']) for p in picks] == [
        (111,347),(348,620)]
    assert all(p['selection_method']=='source_continuous_speech_v1' for p in picks)
