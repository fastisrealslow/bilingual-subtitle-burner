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


def test_623_cover_checks_word_layout_not_just_18_character_count():
    text='片仔癀又呃这个系列产品，他又搞了很多'
    assert not H.cover_fits(text)
    cover=H.cover_copy(text)
    lines=V.cover_headline(cover['text'])
    assert all(len(line)<=9 for line in lines)
    assert H.compact(cover['text']) in H.compact(text) or cover['kind']=='topic_label'


def test_cached_copy_gets_current_cover_layout_without_model(tmp_path,monkeypatch):
    cues=[dict(text='我们长期持有优秀企业。',start=0,end=5)]
    title='林园：我们长期持有优秀企业'
    cache=dict(title=title,cover_title='片仔癀又呃这个系列产品，他又搞了很多',
        copy_identity=dict(version=6,transcript_sha256=P.editorial.text_digest(cues[0]['text']),
            speaker='林园',occasion='访谈',reviewed_title=None))
    (tmp_path/'copywrite.json').write_text(P.json.dumps(cache,ensure_ascii=False))
    monkeypatch.setattr(P,'llm',lambda *a,**k:pytest.fail('Valid title should be reused'))
    result=P.copywrite(cues,[0],'林园','访谈',None,tmp_path)
    assert result['cover_title']=='我们长期持有优秀企业'
    assert result['packaging_version']==H.VERSION


def test_user_reference_titles_keep_concrete_judgments_and_qualifiers():
    examples=[
        '我们作为茅台股东要知道好坏，我不会害你们',
        '科技股赛道机会非常大，但长久下去几乎都是100%的风险',
        '老登股历史性机会！医药布局，核心是慢性病医药股加减重创新药加港股医药',
        '传统消费股几十年便宜，港股可入尤其医药，科技股100%风险',
        '老龄化中药投资逻辑：我觉得我是最厉害的',
    ]
    for text in examples:
        candidates=H.title_candidates(text)
        assert candidates
        assert any(word in candidates[0] for word in H.TOPICS)
    selected=H.title_candidates(examples[1])[0]
    assert '机会非常大' in selected and '长久' in selected and '几乎' in selected and '100%' in selected
    cover=H.cover_copy(selected)
    assert '机会' in cover['text'] and '长期风险' in cover['text']


def test_actual_blood_pressure_title_cannot_keep_padding_or_generic_cover():
    title='林园：问题就是，所以就是我们发现它有这个稳定这个血压的功效'
    assert not H.complete(H.body(title))
    assert H.cover_copy(title)['reason']=='needs_editorial_copy'
    with pytest.raises(ValueError,match='具体封面'):
        H.attach_copy({'title':title},H.body(title))


def test_reviewed_family_anecdote_is_scoped_and_readable():
    title='林园：母亲使用片仔癀的经历，我也不敢给她乱吃这些东西'
    transcript='母亲使用片仔癀。我也不敢给他乱吃这些东西。'
    result=H.attach_copy({'title':title},transcript,
                         reviewed_cover='母亲用片仔癀，不敢乱给她吃')
    assert result['cover_copy']['kind']=='reviewed_editorial'
    assert result['cover_title']!='投资观点'
    assert len(V.cover_headline(result['cover_title']))==2


def test_source_first_still_reads_full_segment_and_never_falls_back_to_keywords(tmp_path,monkeypatch):
    monkeypatch.setenv('SOURCE_EDITORIAL_FIRST','true')
    calls=[]
    def invalid(*args,**kwargs):
        calls.append(args)
        return '{"title":"林园：人少了没办法，它只消费少"}'
    monkeypatch.setattr(P,'llm',invalid)
    result=P.copywrite([dict(text='我们长期持有优秀企业。人少了没办法，它只消费少。',start=0,end=15)],
                    [0],'林园','访谈',None,tmp_path)
    assert len(calls)==3 and result['title_rewrite']['kind']=='editorial_topic'
    assert '没办法' not in result['title']
    assert result['cover_title']==result['title_rewrite']['cover']
