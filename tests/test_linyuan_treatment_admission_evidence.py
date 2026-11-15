"""Two actual admission gaps: long honorific titles and missing attempt evidence."""
from copy import deepcopy
from datetime import datetime
import pytest
from test_linyuan_experiment_duration_probe import F,dispatch_env,candidate


NOW=datetime.fromisoformat('2026-11-15T03:00:00+00:00').timestamp()


def actual_long():
    # Public exact metadata of the 75-minute source measured at 1920x1080 in
    # actual run 43669452779; this fixture grants no media/story approval.
    return dict(id='bilibili_search:BV1224y1N7vy',source='bilibili_collection',
        title='林园 P1 2023.5.24民间股神林园:立足当下 展望未来',
        url='https://www.bilibili.com/video/BV1224y1N7vy',video_url='',author='基金经理说',
        extra=dict(bvid='BV1224y1N7vy',page=1,cid=1141083261,duration=4502,
            collection_title='2023.5.24民间股神林园:立足当下 展望未来',
            source_role='mother_candidate',direct_dispatch=True,no_reprint=0,
            metadata_status='known_duration',metadata_provenance='cached_bilibili_view_exact_id_author_pages',
            metadata_checked_at='2026-11-15T02:53:00+00:00'))


def test_actual_long_source_is_not_a_fragment_just_because_of_the_honorific(monkeypatch):
    monkeypatch.setattr(F.time,'time',lambda:NOW);monkeypatch.setattr(F,'log_event',lambda *a:None)
    item=actual_long();state=dict(dispatched=[],published={},rejected=[],pending_retry=[]);before=deepcopy(state)
    assert F.CLIP_TITLE_PAT.search(item['title']) and not F.FULL_TITLE_PAT.search(item['title'])
    audit={};rows=F.pick([item],state,8,audit=audit)
    assert len(rows)==1 and rows[0]['extra']['duration']==4502
    assert F.experiment.long_candidate(rows[0]) and state==before
    assert not rows[0]['extra'].get('media_verified') and not state['published']


@pytest.mark.parametrize('change',[dict(duration=80),dict(metadata_checked_at='2026-11-12T19:49:51+00:00'),
    dict(metadata_provenance='unverified'),dict(cid=None),dict(page=2),dict(no_reprint=1),
    dict(no_reprint=None),dict(source_role='catalog_only'),dict(direct_dispatch=False)])
def test_short_stale_unverified_wrong_page_or_denied_source_keeps_original_rejection(change):
    item=actual_long();item['extra'].update(change)
    assert F.clip_title_rejected(item,item['extra'],NOW)


@pytest.mark.parametrize('change',[dict(title='股神林园：惊人内幕揭秘'),dict(author=''),
    dict(author='园园滚雪球'),dict(url='https://www.bilibili.com/video/BV1111111111')])
def test_other_clickbait_reference_or_source_identity_stays_blocked(change):
    item={**actual_long(),**change}
    assert F.clip_title_rejected(item,item['extra'],NOW)


def reuse_case(monkeypatch):
    monkeypatch.setattr(F.time,'time',lambda:NOW)
    monkeypatch.setattr(F,'source_inventory',lambda *a:dict(inventory_fresh=True))
    monkeypatch.setattr(F,'inventory_record_current',lambda *a:True)
    monkeypatch.setattr(F,'inventory_publication_error',lambda *a:None)
    url='https://www.bilibili.com/video/BV1224y1N7vy';sha='a'*64
    entry=dict(slug='initial-T',source_url=url,source_sha256=sha,editorial_profile=F.experiment.TREATMENT,ts=1)
    # Actual Nov 15 fallback has verified C parts but no requested/revision
    # evidence in the old inventory projection. Preserve those source fields.
    part=dict(index=0,status='verified',source_sha256=sha,editorial_profile='baseline',
        requested_editorial_profile=F.experiment.TREATMENT,
        experiment_selection_revision=F.experiment.SELECTION_REVISION)
    state=dict(dispatched=[entry],published={});record=dict(slug=entry['slug'],run_id=123,parts=[part])
    stock=dict(artifacts=[record]);items=[dict(page_url=url,extra=dict(duration=4502))]
    return state,stock,items,entry,part


def test_current_initial_treatment_fallback_does_not_earn_a_second_selector_job(monkeypatch):
    state,stock,items,entry,part=reuse_case(monkeypatch);before=deepcopy((state,stock))
    assert list(F.experiment_reuse_candidates(state,stock,items))==[]
    assert (state,stock)==before and part['editorial_profile']=='baseline'


def test_same_mother_mirror_cannot_replay_a_current_completed_attempt(monkeypatch):
    state,stock,items,entry,part=reuse_case(monkeypatch)
    mirror={**entry,'slug':'mirror','source_url':'https://www.bilibili.com/video/BV1111111111',
            'editorial_profile':'baseline'}
    state['dispatched'].append(mirror)
    stock['artifacts'].append(dict(slug='mirror',run_id=456,parts=[{**part,
        'requested_editorial_profile':'baseline','experiment_selection_revision':None}]))
    items.append(dict(page_url=mirror['source_url'],extra=dict(duration=4502)))
    assert list(F.experiment_reuse_candidates(state,stock,items))==[]


@pytest.mark.parametrize('change',[dict(requested_editorial_profile='baseline'),dict(experiment_selection_revision=1)])
def test_real_ordinary_mother_or_proven_older_selector_still_gets_one_current_attempt(monkeypatch,change):
    state,stock,items,entry,part=reuse_case(monkeypatch);part.update(change)
    assert len(list(F.experiment_reuse_candidates(state,stock,items)))==1


def test_new_current_request_with_unknown_outcome_cannot_clone_itself(monkeypatch):
    state,stock,items,entry,part=reuse_case(monkeypatch)
    entry['experiment_selection_revision']=F.experiment.SELECTION_REVISION
    stock['artifacts']=[]
    assert list(F.experiment_reuse_candidates(state,stock,items))==[]


def test_new_automatic_treatment_request_records_current_selector_without_claiming_output(dispatch_env):
    state,candidates,posts,runs=dispatch_env;candidates.append(candidate(1,1800))
    assert F._dispatch_admitted({'execution_backend':'github'})['dispatched']==1
    assert state['dispatched'][0]['experiment_selection_revision']==F.experiment.SELECTION_REVISION
    assert posts[0]['editorial_profile']==F.experiment.TREATMENT and not state['published']
