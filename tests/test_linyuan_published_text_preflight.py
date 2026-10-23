"""Move the same final rejection earlier, without new thresholds or approvals."""
from copy import deepcopy
import inspect
from pathlib import Path
import random
import sys

import pytest

sys.path[:0]=[str(Path(__file__).resolve().parents[1]/'linyuan/fc'),
              str(Path(__file__).resolve().parents[1]/'linyuan')]
import index as F
import produce_cn as P
import publishing_experiment as E
from publication_fingerprints import transcript_duplicate,iter_published_parts


def fp(text):
    return dict(transcript_ngrams=P.transcript_ngram_fingerprints(text),
                transcript_simhash=P.transcript_fingerprints(text))


def original_rule(current,previous):
    # Frozen publisher text policy, before extracting it into the shared helper.
    a=set(current.get('transcript_ngrams') or []);b=set(previous.get('transcript_ngrams') or [])
    shared=len(a & b);ratio=shared/max(1,min(len(a),len(b)))
    if shared>=8 and ratio>=.45:return f'转写片段重合 {ratio:.0%}'
    a=current.get('transcript_simhash') or [];b=previous.get('transcript_simhash') or []
    def distance(x,y):
        try:return (int(x,16)^int(y,16)).bit_count()
        except (TypeError,ValueError):return 10**9
    ratio=sum(any(distance(x,y)<=10 for y in b) for x in a)/len(a) if a and b else 0
    if ratio>=.6 and round(ratio*len(a))>=min(2,len(a)):
        return f'转写内容重合 {ratio:.0%}'
    return None


def test_shared_rule_is_byte_for_byte_equivalent_in_decisions_to_existing_publisher():
    rng=random.Random(20261023)
    for _ in range(250):
        a=dict(transcript_ngrams=rng.sample(list(map(str,range(40))),rng.randrange(40)),
               transcript_simhash=[format(rng.getrandbits(64),'016x') for _ in range(rng.randrange(8))])
        b=deepcopy(a) if rng.randrange(2) else dict(
            transcript_ngrams=rng.sample(list(map(str,range(40))),rng.randrange(40)),
            transcript_simhash=[format(rng.getrandbits(64),'016x') for _ in range(rng.randrange(8))])
        assert transcript_duplicate(a,b)==original_rule(a,b)==F.fingerprint_duplicate(a,b)


@pytest.mark.parametrize('shared,denominator,expected',[(7,7,False),(8,18,False),(9,20,True),(8,8,True)])
def test_existing_ngram_count_and_45_percent_boundary_unchanged(shared,denominator,expected):
    a=dict(transcript_ngrams=list(map(str,range(denominator))))
    b=dict(transcript_ngrams=list(map(str,range(shared)))+['other'+str(i) for i in range(denominator-shared)])
    assert bool(transcript_duplicate(a,b)) is expected


def test_published_iterator_keeps_skipped_duplicate_and_legacy_rules():
    state=dict(published=dict(a=dict(bvid='BV1',title='one',ts=5,fingerprints={'old':True}),
        b=dict(bvid='BV2',title='two',parts=[dict(status='skipped',bvid='skip'),{},{}]),
        c=dict(bvid='BV1',title='one')))
    rows=list(iter_published_parts(state))
    assert rows==list(F.iter_published_parts(state))
    assert [p['bvid'] for _,p in rows]==['BV1','BV2']
    assert rows[0][1]['fingerprints']=={'old':True}


def fixture():
    text='别人曾经多次向我介绍腾讯和苹果公司，可惜当时我没有认真考虑。我听过他们的分析以后，仍然没有参与。'
    cues=[dict(start=200,end=250,text=text)]
    picks=[dict(start=0,end=0)]
    state=dict(dispatched=[],published={'old':dict(source_url='https://different',parts=[dict(
        status='published',bvid='BVpublished',title='old',source_sha256='b'*64,
        source_segments=[dict(start=0,end=50)],fingerprints=fp(text))])})
    report=dict(source_sha256='a'*64,duration_sec=1800)
    return cues,picks,state,report


def test_different_raw_sha_and_url_duplicate_removed_before_classifier_without_source_changes():
    cues,picks,state,report=fixture();before=deepcopy((cues,picks,state,report))
    eligible,excluded=P.story_selection_inputs(cues,picks,report,'https://new-source',state)
    assert not eligible and len(excluded)==1
    assert '文字指纹重复' in excluded[0]['reason'] and 'BVpublished' in excluded[0]['reason']
    assert (cues,picks,state,report)==before


@pytest.mark.parametrize('change',['unrelated','missing_fingerprints','skipped'])
def test_no_text_match_is_not_approval_but_does_not_discard_new_candidate(change):
    cues,picks,state,report=fixture();old=state['published']['old']['parts'][0]
    if change=='unrelated':old['fingerprints']=fp('我们去河边散步，发现春天花开了。孩子们正在开心地追逐蝴蝶，阳光照在树梢。')
    if change=='missing_fingerprints':old.pop('fingerprints')
    if change=='skipped':old['status']='skipped'
    assert P.story_selection_inputs(cues,picks,report,'https://new-source',state)==(picks,[])


def test_exact_authorized_full_clip_partner_still_exempt_but_not_other_published_short():
    cues,picks,state,report=fixture();old=state['published']['old']['parts'][0]
    old.update(content_type='full_interview',editorial_profile=E.BASELINE,source_sha256='a'*64,
        long_story_family=E.family_metadata('a'*64,1800),
        source_segments=[dict(start=0,end=1800)],fingerprints={**old['fingerprints'],'sha256':'c'*64})
    assert P.story_selection_inputs(cues,picks,report,'https://new-source',state)==(picks,[])
    old['content_type']='short_argument'
    assert not P.story_selection_inputs(cues,picks,report,'https://new-source',state)[0]


def test_pre_render_text_check_precedes_any_produce_call_and_final_media_gate_unchanged():
    body=inspect.getsource(P.main)
    assert body.index('text_duplicate=published_transcript_error')<body.index('m = produce_part_with_budget')
    assert 'fingerprints = build_content_fingerprints(final, transcript_text)' in inspect.getsource(P._produce_one)
    assert 'video_ratio >= 0.50' in inspect.getsource(F.fingerprint_duplicate)
    assert 'audio_ratio >= 0.625' in inspect.getsource(F.fingerprint_duplicate)
