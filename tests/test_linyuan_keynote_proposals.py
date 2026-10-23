"""New long-source proposals are source-bound inputs, never cached approvals."""
from copy import deepcopy
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan'))
import keynote_proposals as K


@pytest.fixture(autouse=True)
def current_daily_policy(monkeypatch):
    monkeypatch.setattr(K.editorial,'CONTENT_POLICY','reference_v1')
    monkeypatch.setattr(K.editorial,'MIN_SECONDS',20)


def cues():
    return [dict(start=0,end=10,text='我当年买过这家公司，'),
        dict(start=10,end=20,text='看过产品后才作出这个选择。'),
        dict(start=20,end=40,text='因为产品的需求稳定，我们一直持有。'),
        dict(start=40,end=65,text='公司赚到的钱要能交给股东，这也是我关注的条件。'),
        dict(start=65,end=75,text='您对医药行业有什么看法？')]


def source():return dict(passed=True,source_sha256='a'*64,duration_sec=1200)


def test_proposals_only_offer_whole_source_sentences_and_never_fix_the_text():
    original=cues();before=deepcopy(original);rows=K.windows(original)[0]
    prompt,schema=K.request(rows);props=schema['properties']['picks']['items']['properties']
    assert 0 in props['start']['enum'] and 1 not in props['start']['enum']
    assert 2 not in props['start']['enum'] and 4 not in props['end']['enum']
    assert '原始字幕编号' in prompt
    picks,rejected=K.validate(original,rows,dict(picks=[dict(start=0,end=3)]))
    assert len(picks)==1 and not rejected and picks[0]['start']==0 and picks[0]['end']==3
    assert not any(k in picks[0] for k in ['passed','editorial_review','story_selection','title'])
    assert original==before


@pytest.mark.parametrize('pick',[dict(start=1,end=3),dict(start=2,end=3),dict(start=0,end=4),
    dict(start=True,end=3),dict(start=0,end=100),dict(start=3,end=1)])
def test_cut_sentences_missing_premises_unanswered_questions_and_bad_ids_never_pass(pick):
    with pytest.raises(ValueError):K.validate(cues(),K.windows(cues())[0],dict(picks=[pick]))


def test_overlong_response_is_a_rejected_proposal_not_a_padded_clip():
    original=cues();original[3]['end']=400;original[4].update(start=400,end=410)
    picks,rejected=K.validate(original,K.windows(original)[0],dict(picks=[dict(start=0,end=3)]))
    assert not picks and rejected[0]['reason']=='duration_outside_source_policy'


def test_actual_context_dependent_openings_from_run_39928062896_are_not_available():
    for text in ['因为本身我不是老师，也不也不这个不是这个。',
                 '啊，他这个投资啊，实际上你要说真正的投资是什么含义？',
                 '因为出生年，我们那一代，我这一代人。',
                 '啊，所以我们，我经常强调我们只投嘴巴。']:
        assert not K.opening_allowed(text)
    assert K.opening_allowed('我当年买了这家公司，是看过它的产品以后才决定的。')


def test_actual_new_deictic_preamble_is_rejected_without_erasing_good_neighbour():
    original=cues()[:4]+[dict(start=65,end=100,text='这是我的看法，太多了。'),
        dict(start=100,end=140,text='市场里的供给和需求需要时间才能重新平衡。')]
    picks,rejected=K.validate(original,K.windows(original)[0],dict(picks=[
        dict(start=0,end=3),dict(start=4,end=5)]))
    assert [(p['start'],p['end']) for p in picks]==[(0,3)]
    assert rejected==[dict(start=4,end=5,reason='opening_only_refers_to_a_previous_view')]


def test_window_limits_overlap_and_original_sentence_endpoints_are_preserved(monkeypatch):
    monkeypatch.setattr(K,'MAX_WINDOW_CHARS',180)
    original=[dict(start=i*20,end=(i+1)*20,text='我们研究的这家公司有自己的产品和稳定需求。') for i in range(20)]
    result=K.windows(original)
    assert len(result)>1
    assert set(r['start'] for group in result for r in group)==set(range(20))
    assert all(sum(len(r['description'])+1 for r in group)<=180 for group in result)
    assert all(group[0]['start']<later[0]['start'] for group,later in zip(result,result[1:]))
    assert any(set(r['start'] for r in group)&set(r['start'] for r in later)
               for group,later in zip(result,result[1:]))
    oversized=[dict(start=0,end=1000,text='我们研究投资'*100+'。')]
    assert K.request(K.windows(oversized)[0]) is None


def test_replay_revalidates_actual_response_not_saved_picks_or_approval_flags(tmp_path):
    path=tmp_path/'keynote_proposals.json';original=cues();calls=[]
    def call(prompt,schema,budget):
        calls.append(budget);return json.dumps(dict(picks=[dict(start=0,end=3)]))
    result=K.propose(original,source(),call,path)
    assert result['selection_completed'] and len(result['picks'])==1 and result['media_approved'] is False
    assert len(calls)==1 and 0<calls[0]<=600
    saved=json.loads(path.read_text());saved['picks']=[dict(start=1,end=4,passed=True)]
    saved['media_approved']=True;path.write_text(json.dumps(saved))
    result=K.propose(original,source(),lambda *a:pytest.fail('Exact valid response should be reused'),path)
    assert [(p['start'],p['end']) for p in result['picks']]==[(0,3)]
    assert result['media_approved'] is False and result['calls'][0]['cache_reused'] is True
    saved=json.loads(path.read_text());saved['calls'][0]['response']='{"picks":[{"start":2,"end":3}]}'
    path.write_text(json.dumps(saved));result=K.propose(original,source(),lambda *a:pytest.fail('Validate cached response'),path)
    assert not result['picks'] and result['unresolved'] and not result['selection_completed']


def test_source_or_transcript_change_never_reuses_a_cached_proposal(tmp_path):
    path=tmp_path/'keynote_proposals.json';call=lambda *a:'{"picks":[]}'
    K.propose(cues(),source(),call,path)
    changed=source();changed['source_sha256']='b'*64;called=[]
    def fresh(*a):called.append(True);return '{"picks":[]}'
    K.propose(cues(),changed,fresh,path);assert called==[True]
    original=cues();original[0]['text']='我买过这家企业，'
    K.propose(original,changed,fresh,path);assert len(called)==2


def test_invalid_or_timed_out_inference_stays_unresolved_and_is_not_replayed_as_empty(tmp_path):
    path=tmp_path/'keynote_proposals.json'
    result=K.propose(cues(),source(),lambda *a:'not JSON',path)
    assert not result['selection_completed'] and result['unresolved'] and not result['picks']
    result=K.propose(cues(),source(),lambda *a:'{"picks":[]}',path)
    assert result['selection_completed'] and not result['unresolved']
    result=K.propose(cues(),source(),lambda *a:pytest.fail('No request after budget'),
                     tmp_path/'timeout.json',budget_sec=0)
    assert not result['selection_completed'] and result['unresolved'] and not result['picks']


@pytest.mark.parametrize('change',[dict(passed=False),dict(source_sha256='wrong'),
    dict(duration_sec=899),dict(duration_sec=5401),dict(duration_sec=float('nan'))])
def test_only_a_fresh_checked_long_mother_can_use_proposals(tmp_path,change):
    s=source();s.update(change)
    with pytest.raises(ValueError):K.propose(cues(),s,lambda *a:pytest.fail('Unverified source'),tmp_path/'p.json')


@pytest.mark.parametrize('profile,width,height,expected',[
    ('baseline',1920,1080,True),('field-story-v1',1920,1080,True),
    ('field-story-v1',852,480,False)])
def test_actual_cli_offers_only_new_long_source_ranges_and_keeps_impossible_t_fast(tmp_path,monkeypatch,profile,width,height,expected):
    import produce_cn as P
    import publishing_experiment as E
    import source_selection as S
    monkeypatch.setenv('LINYUAN_AUTOMATIC_ONLY','true')
    monkeypatch.setenv('SOURCE_EDITORIAL_FIRST','true')
    monkeypatch.setenv('LINYUAN_EDITORIAL_PROFILE',profile)
    monkeypatch.delenv('SOURCE_VISUAL_CHAPTERS',raising=False)
    monkeypatch.delenv('PUBLICATION_STATE_PATH',raising=False)
    monkeypatch.setattr(P,'BASE',tmp_path)
    monkeypatch.setattr(P,'TEXT_BACKEND','local')
    monkeypatch.setattr(P,'load_key',lambda:None)
    original=cues();report={**source(), 'visual_identity':{},'resolution':dict(width=width,height=height)}
    monkeypatch.setattr(P,'run_source_quality_gate',lambda *a:report)
    monkeypatch.setattr(P,'transcribe',lambda *a:original)
    monkeypatch.setattr(S,'select',lambda *a,**k:[])
    invoked=[];pick=dict(start=0,end=3,score=7,reason='Source-only candidate')
    def propose(c,s,call,path):
        assert c is original and s is report
        invoked.append(True);return dict(picks=[pick],unresolved=[],selection_completed=True)
    monkeypatch.setattr(K,'propose',propose)
    monkeypatch.setattr(P,'story_selection_inputs',lambda *a:([pick],[]))
    monkeypatch.setattr(E,'choose_stories',lambda *a:dict(selected=[],unresolved=[]))
    monkeypatch.setattr(P,'pick_highlights',lambda *a,**k:[])
    video=tmp_path/'source.mp4';video.write_bytes(b'CLI fixture, not approved media')
    monkeypatch.setattr(P.sys,'argv',['produce_cn','--source',str(video),'--slug','proposal',
        '--split-highlights','--dry-run'])
    assert P.main()==0
    assert bool(invoked)==expected
    assert not list(tmp_path.rglob('final*.mp4'))


def test_incomplete_proposal_is_a_retryable_source_nonverdict_in_actual_batch(tmp_path,monkeypatch):
    import produce_cn as P
    import source_selection as S
    monkeypatch.setenv('LINYUAN_AUTOMATIC_ONLY','true')
    monkeypatch.setenv('SOURCE_EDITORIAL_FIRST','true')
    monkeypatch.setenv('LINYUAN_EDITORIAL_PROFILE','baseline')
    monkeypatch.delenv('SOURCE_VISUAL_CHAPTERS',raising=False)
    monkeypatch.delenv('PUBLICATION_STATE_PATH',raising=False)
    monkeypatch.setattr(P,'BASE',tmp_path);monkeypatch.setattr(P,'TEXT_BACKEND','local')
    monkeypatch.setattr(P,'load_key',lambda:None)
    report={**source(),'visual_identity':{},'resolution':dict(width=1920,height=1080)}
    monkeypatch.setattr(P,'run_source_quality_gate',lambda *a:report)
    monkeypatch.setattr(P,'transcribe',lambda *a:cues())
    monkeypatch.setattr(S,'select',lambda *a,**k:[])
    monkeypatch.setattr(K,'propose',lambda *a,**k:dict(picks=[],unresolved=[dict(window=0)],selection_completed=False))
    monkeypatch.setattr(P,'pick_highlights',lambda *a,**k:[])
    video=tmp_path/'source.mp4';video.write_bytes(b'CLI fixture, not approved media')
    monkeypatch.setattr(P.sys,'argv',['produce_cn','--source',str(video),'--slug','incomplete','--split-highlights'])
    assert P.main()==2
    batch=json.loads((tmp_path/'deliver/incomplete/batch_report.json').read_text())
    assert batch['accepted']==0 and batch['retryable'] is True and batch['selection_completed'] is False
    assert batch['experiment_production']['retryable_selection'] is True
    assert any(r['error_type']=='KeynoteProposalIncomplete' and r['retryable'] for r in batch['rejected'])


@pytest.mark.parametrize('proposed,explicit_limit,expected_attempts,expected_accepted',[
    (True,0,3,2),(True,1,2,1),(False,0,5,4)])
def test_delivery_limit_counts_real_acceptances_and_retains_failed_candidate_backups(
        tmp_path,monkeypatch,proposed,explicit_limit,expected_attempts,expected_accepted):
    import produce_cn as P
    import source_selection as S
    monkeypatch.setenv('LINYUAN_AUTOMATIC_ONLY','true');monkeypatch.setenv('SOURCE_EDITORIAL_FIRST','true')
    monkeypatch.setenv('LINYUAN_EDITORIAL_PROFILE','baseline')
    monkeypatch.delenv('SOURCE_VISUAL_CHAPTERS',raising=False);monkeypatch.delenv('PUBLICATION_STATE_PATH',raising=False)
    monkeypatch.setattr(P,'BASE',tmp_path);monkeypatch.setattr(P,'TEXT_BACKEND','local')
    monkeypatch.setattr(P,'load_key',lambda:None)
    original=[dict(start=i*40,end=(i+1)*40,text=f'候选{i}的原文观点和理由均须独立验收。') for i in range(5)]
    picks=[dict(start=i,end=i,score=7,reason='Test-only source candidate') for i in range(5)]
    report={**source(),'visual_identity':{},'resolution':dict(width=1920,height=1080)}
    monkeypatch.setattr(P,'run_source_quality_gate',lambda *a:report)
    monkeypatch.setattr(P,'transcribe',lambda *a:original)
    monkeypatch.setattr(S,'select',lambda *a,**k:[] if proposed else picks)
    monkeypatch.setattr(K,'propose',lambda *a,**k:dict(picks=picks,unresolved=[],selection_completed=True))
    calls=[]
    def produce(src,work,out,seg_cues,*args,**kwargs):
        calls.append(seg_cues)
        if len(calls)==1:raise P.VisualQualityError('Test-only rejected first picture')
        return dict(final=f'test-only-{len(calls)}.mp4',title=f'独立通过的测试元数据{len(calls)}',
            render_mode='live_video_card',resolution=dict(width=720,height=1280),duration_sec=40)
    monkeypatch.setattr(P,'produce_part_with_budget',produce)
    video=tmp_path/'source.mp4';video.write_bytes(b'CLI fixture, not approved media')
    monkeypatch.setattr(P.sys,'argv',['produce_cn','--source',str(video),'--slug','limited',
        '--split-highlights','--max-outputs',str(explicit_limit)])
    assert P.main()==0
    batch=json.loads((tmp_path/'deliver/limited/batch_report.json').read_text())
    assert len(calls)==expected_attempts and batch['accepted']==expected_accepted
    assert batch['part_attempts'][0]['outcome']=='rejected'
    assert not list(tmp_path.rglob('final*.mp4'))
