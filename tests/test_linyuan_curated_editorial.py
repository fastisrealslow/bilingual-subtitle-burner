import json
from pathlib import Path
import sys
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan'))
from curated_editorial import source_ranges


def test_reviewed_ranges_bind_source_boundaries_and_duration(tmp_path):
    path=tmp_path/'profile.json'
    path.write_text(json.dumps({'sources':{'source':[
        dict(start=1,end=122,opening='独立话题',ending='完整结论',topic='讨论')
    ]}}))
    cues=[dict(start=1,end=10,text='独立话题'),dict(start=12,end=122,text='完整结论')]
    assert source_ranges(cues,'other',path) is None
    assert source_ranges(cues,'source',path)[0][:2]==(0,1)
    cues[-1]['end']=42
    with pytest.raises(ValueError,match='duration or sentence boundary'):
        source_ranges(cues,'source',path)
    cues[-1].update(end=122,text='半句话')
    with pytest.raises(ValueError,match='opening or ending'):
        source_ranges(cues,'source',path)


def test_known_single_omission_preserves_source_sentence_boundaries(tmp_path):
    source='9dc2b7c6f82570984a52ccdff5c4a41a7595c0a129b1919df81d7539a266a345'
    path=tmp_path/'profile.json'
    path.write_text(json.dumps(dict(sources={source:[dict(start=938.6,end=1121.16,
        opening='完整话题',ending='完整结论',topic='讨论',omit=dict(start=1037.16,end=1044.44))]})))
    cues=[dict(start=938.6,end=1037.16,text='完整话题'),
          dict(start=1037.56,end=1043.56,text='未说完的插语'),
          dict(start=1044.44,end=1121.16,text='完整结论')]
    a,b,picks=source_ranges(cues,source,path)[0]
    assert (a,b)==(0,2)
    assert [(p['start'],p['end']) for p in picks]==[(0,0),(2,2)]
    cues[2]['start']+=1
    with pytest.raises(ValueError,match='omission'):
        source_ranges(cues,source,path)


def test_reviewed_subtitle_groups_are_passed_to_renderer(tmp_path):
    path=tmp_path/'profile.json'
    path.write_text(json.dumps({'sources':{'source':[
        dict(start=1,end=122,opening='独立话题',ending='完整结论',topic='讨论',
             subtitle_groups=['独立话题','完整结论'])
    ]}}))
    cues=[dict(start=1,end=10,text='独立话题'),dict(start=12,end=122,text='完整结论')]
    picks=source_ranges(cues,'source',path)[0][2]
    assert picks[0]['editorial_subtitles']==['独立话题','完整结论']
    assert picks[0]['editorial_source_sha256']=='source'


def test_reviewed_argument_is_bound_to_exact_corrected_transcript(monkeypatch,tmp_path):
    import produce_cn as production
    from editorial_policy import VERSION,text_digest
    cues=[dict(start=1,end=10,text='独立话题。'),dict(start=12,end=122,text='完整结论。')]
    transcript=''.join(c['text'] for c in cues)
    review=dict(version=VERSION,standalone_opening=True,complete_argument=True,
        reasoning_present=True,natural_ending=True,requires_audio_review=False,
        summary='独立话题包含理由并得出完整结论',issues=[],issue_details=[],
        opening_quote='独立话题。',ending_quote='完整结论。',
        transcript_sha256=text_digest(transcript),review_protocol=2,review_prompt_version=2)
    pick=dict(start=0,end=1,editorial_source_sha256='source',editorial_review=review)
    monkeypatch.setattr(production,'llm',lambda *a,**k: (_ for _ in ()).throw(AssertionError('no llm')))
    assert production.review_complete_argument(cues,[pick],'林园','',tmp_path,'')['summary']
    pick['editorial_review']={**review,'transcript_sha256':'stale'}
    with pytest.raises(production.VisualQualityError,match='不匹配'):
        production.review_complete_argument(cues,[pick],'林园','',tmp_path/'stale','')
