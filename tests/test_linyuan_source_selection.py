"""Uncached source selection must not depend on an Ollama response."""
import json
import sys
from pathlib import Path
from unittest.mock import patch
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan'))
import produce_cn as p
from source_selection import select
from live_tracking import frame_interval


def dialogue():
    return [dict(start=0,end=10,text='您对医药股有什么判断？'),
        dict(start=10,end=55,text='医药行业需求随着老龄化增长，我们长期持有这些企业。'),
        dict(start=55,end=100,text='但是投资仍然有风险，价格和需求都要看，不能只看过去。'),
        dict(start=100,end=145,text='企业产品有需求，投资才有长期增长的基础。'),
        dict(start=145,end=150,text='您对科技股怎么看？')]


def test_real_question_and_answer_keep_every_condition_and_timestamp(tmp_path,monkeypatch):
    cues=dialogue(); before=json.dumps(cues,ensure_ascii=False)
    monkeypatch.setenv('SOURCE_EDITORIAL_FIRST','true')
    with patch.object(p,'llm',side_effect=AssertionError('model must not be required')):
        picks=p.pick_highlights(cues,'林园','',tmp_path)
        assert [(r['start'],r['end']) for r in picks]==[(0,3)]
        copy=p.copywrite(cues,list(range(4)),'林园','访谈','',tmp_path)
        assert copy['title_quality_verified']
    assert json.dumps(cues,ensure_ascii=False)==before
    assert '但是投资仍然有风险' in ''.join(c['text'] for c in cues[:4])


def test_short_answer_cannot_borrow_next_question_to_reach_120():
    cues=dialogue();cues[3]['end']=115;cues[4]['start']=115
    assert select(cues)==[]


def test_mechanical_chunk_end_is_not_an_answer_boundary():
    assert select(dialogue()[:-1])==[]
    assert select(dialogue()[:-1],whole_source=True)


def test_unfinished_asr_tail_and_unanswered_question_cannot_supply_duration():
    cues=dialogue()[:3]+[dict(start=100,end=160,text='但是如果企业的')]
    assert select(cues,whole_source=True)==[]


def test_known_third_party_audio_cannot_reuse_a_face_approval(tmp_path):
    bad='87e4dcea6b1292f184edb15188c38c4075a4fa94fc1b272ac2fc385f862faff1'
    with patch.object(p,'_file_sha256',return_value=bad),patch.object(p,'verified_source_evidence') as cache:
        report=p.run_source_quality_gate(Path('source.mp4'),tmp_path,'林园','')
        assert report['passed'] is False and report['retryable'] is False
        cache.assert_not_called()
        with pytest.raises(p.VisualQualityError,match='王红'):
            p.load_source_quality_report(Path('source.mp4'),tmp_path/'source_quality.json')
    assert '王红' in p.editorial.metadata_error(dict(speaker='林园',source_sha256=bad))


def test_seek_and_eof_use_same_grid_without_padding():
    assert frame_interval(27.2,275.08,25,7556)==(680,6876)
    with pytest.raises(ValueError,match='短'):
        frame_interval(27.2,280,25,7556)


def test_actual_mp4_decodes_exactly_the_planned_frames(tmp_path):
    import subprocess,shutil
    if not shutil.which('ffmpeg'):pytest.skip('Media test runs after ffmpeg installation')
    cv2=pytest.importorskip('cv2')
    video=tmp_path/'rounding.mp4'
    subprocess.run(['ffmpeg','-loglevel','error','-f','lavfi','-i',
        'testsrc2=size=64x64:rate=25:duration=1','-c:v','libx264','-y',str(video)],check=True)
    cap=cv2.VideoCapture(str(video))
    first,count=frame_interval(.2,.82,cap.get(cv2.CAP_PROP_FPS),round(cap.get(cv2.CAP_PROP_FRAME_COUNT)))
    cap.set(cv2.CAP_PROP_POS_FRAMES,first)
    assert sum(cap.read()[0] for _ in range(count))==count
    assert not cap.read()[0]
    cap.release()


def test_interviewer_clauses_cannot_shed_question_context_for_a_title():
    from headline_policy import title_candidates
    text='呃，您看好三种药品，然后你也聊聊原因，是因为受中国人口老龄化的影响嘛。我们看好的企业必须有持续增长的需求。'
    titles=title_candidates(text)
    assert titles and all('老龄化' not in title for title in titles)
    assert p.editorial.title_attribution_error('林园：然后你也聊聊到说看好这三个药品的原因')
