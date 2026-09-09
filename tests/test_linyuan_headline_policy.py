"""Regressions from the 30-day cover/title audit; no network or model calls."""
import sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan'))
import headline_policy as H
import presentation as V
import produce_cn as P


def test_real_truncated_cover_uses_complete_clause_not_ellipsis():
    title='林园：现在消费和医药的回报是我从事资本市场最值得的最好的时候，曙光就在眼前'
    result=H.cover_copy(title)
    assert '…' not in result['text'] and '资本' not in result['text']
    assert len(''.join(V.cover_headline(result['text'])))<=18
    assert result['kind'] in {'topic_label','extractive_label'} or H.compact(result['text']) in H.compact(title)


def test_half_opening_is_not_a_cover_quote():
    title='林园：我们说的买入，你要投重手的话一定择时非常重要'
    result=H.cover_copy(title)
    assert result['text']!='我们说的买入'
    assert '…' not in result['text']


def test_qualified_negative_cannot_become_unqualified_positive():
    title='林园：如果价格始终没有跌到我们能承受的水平，我们不会买入'
    result=H.cover_copy(title)
    assert result['kind']=='topic_label' # No short quote preserves this condition.
    assert all('如果' in x for x in H.title_candidates(title) if x.endswith('我们不会买入'))


def test_short_quote_retains_negation_and_number():
    result=H.cover_copy('林园：股息率不到8%我不会买')
    assert result['text']=='股息率不到8%我不会买'
    assert ''.join(V.cover_headline(result['text']))==result['text']


def test_complete_later_quote_beats_vague_opening_without_numeric_requirement():
    transcript='我们说的买入。我们长期持有优秀企业。企业投得太多最后走向绝境。'
    rows=H.title_candidates(transcript)
    assert rows and all('我们说的买入' not in x for x in rows)
    assert all(H.compact(H.body(x)) in H.compact(transcript) for x in rows)


def test_independent_cover_and_candidates_are_saved_for_review():
    transcript='市场的股息率有8%，我买过，现在连吃的喝的都有了，这位置低的不得了。我们长期持有优秀企业。'
    result=H.attach_copy({'title':'林园：'+transcript.split('。')[0]},transcript)
    assert result['title']!=result['cover_title']
    assert len(result['title_candidates'])==3
    assert result['cover_copy']['kind'] in {'quote','extractive_label'}
    assert '8%' in result['cover_title']


def test_reviewed_copy_cache_refreshes_old_cover_policy(tmp_path):
    title='林园：所有的产品都会变得一文不值'
    cues=[{'text':'所有的产品都会变得一文不值。','start':0,'end':6}]
    result=P.copywrite(cues,[0],'林园','访谈',None,tmp_path,reviewed_title=title)
    assert result['title']==title and result['packaging_version']==H.VERSION
    assert result['cover_title']=='所有的产品都会变得一文不值'


def test_big_frequent_host_cannot_beat_verified_guest(tmp_path,monkeypatch):
    import cv2
    import numpy as np
    reference=tmp_path/'ref.jpg';reference.touch()
    frames=[tmp_path/'host.jpg',tmp_path/'interview.jpg']
    monkeypatch.setattr(P,'_local_face_models',lambda:('detector','recognizer'))
    # Pixel id chooses the detection fixture; trailing value stands for identity score.
    monkeypatch.setattr(cv2,'imread',lambda path:np.full((400,600,3),
        0 if path.endswith('ref.jpg') else 1 if path.endswith('host.jpg') else 2,dtype=np.uint8))
    class Detector:
        def setInputSize(self,*_):pass
        def detect(self,im):
            return True,np.array({0:[[0,0,120,120,.9]],1:[[10,10,280,280,.2]],
                2:[[10,10,280,280,.2],[350,80,100,100,.8]]}[int(im[0,0,0])])
    class Recognizer:
        def alignCrop(self,im,face):return face[-1]
        def feature(self,aligned):return aligned
        def match(self,ref,feature,*_):return feature
    monkeypatch.setattr(cv2.FaceDetectorYN,'create',lambda *a,**k:Detector())
    monkeypatch.setattr(cv2.FaceRecognizerSF,'create',lambda *a,**k:Recognizer())
    path,box,proof=P.select_verified_cover_face(frames,reference)
    assert path.name=='interview.jpg' and box==(350,80,100,100)
    assert proof['cosine_score']==.8 and proof['matched_faces']==1


def test_extractive_cover_never_drops_negation_or_uncertainty():
    text='林园：消费和医药的回报不是我从事资本市场以来最值得的时候'
    result=H.cover_copy(text)
    assert result['kind']=='topic_label'
    assert '最值得' not in result['text']


def test_full_interview_cover_reports_actual_length_without_text_model(tmp_path,monkeypatch):
    monkeypatch.setattr(P,'llm',lambda *a,**k:pytest.fail('full format must not require a model'))
    result=P.copywrite([dict(text='我们长期持有优秀企业',start=0,end=3472)],[0],
                      '林园','访谈',None,tmp_path,suffix='_full',require_quote=False)
    assert result['cover_title']=='58分钟完整访谈'
    assert result['title']=='林园：58分钟完整访谈原声'


def test_labeled_transcript_cannot_duplicate_speaker_prefix():
    candidates=H.title_candidates('林园：我们长期持有优秀企业。')
    assert candidates and all('林园：林园' not in t for t in candidates)
