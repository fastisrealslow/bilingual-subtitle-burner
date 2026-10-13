import json
from pathlib import Path
import sys
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan'))
import source_topic_discovery as D


def cues(count=12):
    return [dict(start=i*30,end=(i+1)*30,text=f'企业投资需要研究长期需求，这是第{i}个原始完整句。') for i in range(count)]


def test_windows_preserve_complete_sentence_starts_and_never_label_chunk_edges_natural():
    original=cues(100);before=json.dumps(original)
    windows=D.windows(original,max_seconds=1200)
    assert len(windows)==3
    assert windows[0]['natural_start'] and not windows[0]['natural_end']
    assert not windows[1]['natural_start'] and not windows[1]['natural_end']
    assert windows[-1]['natural_end'] and not windows[-1]['natural_start']
    assert windows[1]['start']<windows[0]['end']
    assert json.dumps(original)==before


def test_mechanical_window_first_and_last_topics_cannot_become_source_endings(monkeypatch):
    monkeypatch.setattr(D.selection,'boundary_error',lambda *_:None)
    original=cues()
    window=dict(start=2,end=9,natural_start=False,natural_end=False)
    topics=[dict(start=0,end=1,topic='前方还在继续'),dict(start=2,end=5,topic='完整话题'),dict(start=6,end=7,topic='后方还在继续')]
    picks=D.topic_ranges(original,window,topics)
    assert [(p['start'],p['end']) for p in picks]==[(4,7)]
    assert picks[0]['editorial_approved'] is False


def test_actual_source_edges_only_are_available_to_independent_review(monkeypatch,tmp_path):
    monkeypatch.setattr(D.selection,'boundary_error',lambda *_:None)
    original=cues(6);before=json.dumps(original)
    def call(prompt,schema):
        assert '不是自然观点边界' in prompt
        assert schema['properties']['topics']['items']['properties']['start']['enum']==list(range(6))
        return json.dumps(dict(topics=[dict(start=0,topic='需求'),dict(start=3,topic='风险')]))
    picks=D.discover(original,call,tmp_path,'a'*64)
    assert [(p['start'],p['end']) for p in picks]==[(0,2),(3,5)]
    assert json.dumps(original)==before
    assert not json.loads((tmp_path/'topic-discovery.json').read_text())['editorial_approved']
    assert all('passed' not in p for p in picks)


@pytest.mark.parametrize('response',[
    '{"topics":[{"start":1,"topic":"漏开头"}]}',
    '{"topics":[{"start":0,"topic":"甲"},{"start":0,"topic":"乙"}]}',
    '{"topics":[],"topics":[{"start":0,"topic":"覆盖重复字段"}]}',
])
def test_bad_or_duplicate_model_ranges_are_recoverable_not_quality_rejections(tmp_path,response):
    with pytest.raises(RuntimeError,match='服务未完成'):
        D.discover(cues(6),lambda *_:response,tmp_path,'a'*64)
    proof=json.loads((tmp_path/'topic-discovery.json').read_text())
    assert proof['reviewed_windows'][0]['service_error'] and not proof['picks']


def test_service_failure_in_later_window_keeps_earlier_valid_proposal(monkeypatch,tmp_path):
    monkeypatch.setattr(D.selection,'boundary_error',lambda *_:None)
    original=cues(100);calls=[]
    def call(prompt,schema):
        calls.append(1)
        if len(calls)>1:raise TimeoutError('CPU timed out')
        return json.dumps(dict(topics=[dict(start=0,topic='首个主话题'),dict(start=3,topic='完整主话题'),dict(start=8,topic='窗口边缘')]))
    picks=D.discover(original,call,tmp_path,'a'*64)
    assert len(calls)==3
    assert [(p['start'],p['end']) for p in picks]==[(0,2),(3,7)]
    assert json.loads((tmp_path/'topic-discovery.json').read_text())['reviewed_windows'][1]['service_error']


def test_oversized_sentence_is_not_cut_or_completed_with_guessed_text():
    original=[dict(start=0,end=2000,text='这句话太长但仍然是一句。'),dict(start=2000,end=2040,text='下一个完整句。')]
    assert D.windows(original)==[dict(start=1,end=1,natural_start=False,natural_end=True)]


def test_missing_final_sentence_punctuation_is_not_an_accepted_natural_end(monkeypatch):
    original=cues(3)+[dict(start=90,end=120,text='但是后面的话尚未')]
    window=D.windows(original)[0]
    assert not window['natural_end']
    assert D.topic_ranges(original,window,[dict(start=0,end=2,topic='尚未结束')])==[]
