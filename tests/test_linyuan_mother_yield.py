import copy
from pathlib import Path
import sys
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'linyuan'))
from mother_yield import canonical, duration_evidence, summarize, apply_frozen_goal, positive

END = 2_000_000
SHA, SOURCE = 'a'*64, 'b'*64
URL = 'https://www.bilibili.com/video/BV1NpBFYkEKx'


def fixture(status='failed'):
    state = dict(dispatched=[dict(slug='job',ts=END-100,source_url=URL)],published={})
    inventory = dict(validation_sha256='c'*64,quality_gate_version=12,artifacts=[])
    snapshot = dict(updated_at=END,tasks=[dict(slug='job',status=status,
        signature=dict(ts=END-100),run_id=123,detail='no accepted clip')])
    evidence = {'BV1NpBFYkEKx:p1': dict(seconds=[1800],basis='live_exact_page')}
    return state, inventory, snapshot, evidence


def part(**updates):
    value = dict(status='verified',sha256=SHA,source_sha256=SOURCE,render_mode='live_video_card',
        resolution=dict(width=720,height=1280))
    value.update(updates)
    return value


def inspected(inventory, **updates):
    inventory['artifacts'] = [dict(slug='job',run_id=123,checked_at=END-20,parts=[part(**updates)])]


def receipt(state, **updates):
    value = part(status='published',bvid='BV1LQVc6eEFu',ts=END-10,fingerprints=dict(sha256=SHA))
    value.update(updates)
    state['published']['job'] = dict(parts=[value])


def test_complete_or_successful_jobs_alone_are_not_accepted_clips():
    result = summarize(*fixture('complete'))
    assert result['observed_success_rate'] == 0
    assert result['cohort']['completed_known_long'] == 1
    assert not result['target_is_feasible'], 'Twice zero is not a useful attainable objective'


def test_inspected_and_published_same_file_counts_once():
    state, inventory, snapshot, durations = fixture('ready')
    inspected(inventory); receipt(state)
    result = summarize(state, inventory, snapshot, durations)
    assert result['accepted_unique_clip_count'] == 1
    assert result['cohort']['successful_known_long'] == 1
    assert result['observed_success_rate'] == 1 and result['target_rate'] == 2
    assert not result['target_is_feasible'], 'Never silently cap a doubled 100% rate'


@pytest.mark.parametrize('changes', [dict(status='rejected'),dict(render_mode='audio_card'),
    dict(sha256=None),dict(source_sha256=None),dict(resolution=dict(width=360,height=640))])
def test_producer_flags_bad_hashes_audio_cards_and_low_res_are_not_yield(changes):
    state, inventory, snapshot, durations = fixture('ready')
    inspected(inventory,**changes)
    assert summarize(state, inventory, snapshot, durations)['cohort']['successful_known_long'] == 0


def test_raw_approval_flags_missing_independent_inspection_are_not_evidence():
    state, inventory, snapshot, durations = fixture('ready')
    inspected(inventory); inventory.pop('validation_sha256')
    assert summarize(state, inventory, snapshot, durations)['accepted_unique_clip_count'] == 0


def test_wrong_run_or_future_acceptance_never_borrows_another_attempts_output():
    state, inventory, snapshot, durations = fixture('ready')
    inspected(inventory); inventory['artifacts'][0]['run_id'] = 999
    receipt(state,ts=END+1)
    assert summarize(state, inventory, snapshot, durations)['accepted_unique_clip_count'] == 0


def test_retries_and_tracking_parameters_are_one_source_but_p2_is_another():
    state, inventory, snapshot, durations = fixture('ready'); inspected(inventory)
    state['dispatched'].extend([dict(slug='retry',ts=END-50,source_url=URL+'?p=1&from=search'),
        dict(slug='page2',ts=END-40,source_url=URL+'?p=2')])
    snapshot['tasks'].extend([dict(slug='retry',status='failed',signature=dict(ts=END-50)),
        dict(slug='page2',status='failed',signature=dict(ts=END-40))])
    durations['BV1NpBFYkEKx:p2'] = dict(seconds=[1200])
    result = summarize(state, inventory, snapshot, durations)
    assert result['cohort']['completed_known_long'] == 2
    assert result['cohort']['successful_known_long'] == 1
    assert result['observed_success_rate'] == .5


def test_unknown_durations_do_not_flatter_the_complete_cohort_rate():
    state, inventory, snapshot, durations = fixture('ready'); inspected(inventory)
    state['dispatched'].append(dict(slug='unknown',ts=END-30,source_url='https://example.com/unknown'))
    snapshot['tasks'].append(dict(slug='unknown',status='failed',signature=dict(ts=END-30)))
    result = summarize(state, inventory, snapshot, durations)
    assert result['cohort']['completed_duration_unknown'] == 1
    assert result['observed_success_rate'] == 1
    assert result['possible_all_completed_rate_bounds'] == dict(lower=.5,upper=1)


def test_active_partial_batches_stale_projections_and_outside_window_stay_out_of_denominator():
    state, inventory, snapshot, durations = fixture('running'); inspected(inventory)
    result = summarize(state, inventory, snapshot, durations)
    assert result['cohort']['completed_known_long'] == 0
    assert result['cohort']['active_or_unresolved'] == 1
    snapshot['tasks'][0]['status'] = 'complete'
    snapshot['tasks'][0]['signature']['ts'] -= 1
    assert summarize(state, inventory, snapshot, durations)['observed_success_rate'] is None
    state['dispatched'][0]['ts'] = END-8*86400
    assert summarize(state, inventory, snapshot, durations)['cohort']['unique_attempted_sources'] == 0


def test_exact_page_duration_beats_aggregate_total_and_rejects_unknown_view():
    items = [dict(url=URL,extra=dict(duration=1866))]
    pages = [dict(bvid='BV1NpBFYkEKx',status='verified_exact_pages',pages=[
        dict(page=1,cid=10,duration=898),dict(page=2,cid=20,duration=968)])]
    result = duration_evidence(items, exact_pages=pages)
    assert result['BV1NpBFYkEKx:p1']['seconds'] == [898]
    assert result['BV1NpBFYkEKx:p2']['seconds'] == [968]
    pages[0]['status'] = 'unknown'
    assert duration_evidence(items,exact_pages=pages)['BV1NpBFYkEKx:p1']['seconds'] == [1866]


def test_conflicting_clocks_on_both_sides_of_threshold_remain_unknown():
    state, inventory, snapshot, durations = fixture()
    durations['BV1NpBFYkEKx:p1']['seconds'] = [898,1866]
    result = summarize(state, inventory, snapshot, durations)
    assert result['cohort']['completed_known_long'] == 0
    assert result['cohort']['completed_duration_unknown'] == 1


def test_report_has_no_signed_url_and_never_mutates_input():
    state, inventory, snapshot, durations = fixture()
    original = copy.deepcopy((state,inventory,snapshot,durations))
    summarize(state, inventory, snapshot, durations)
    assert (state,inventory,snapshot,durations) == original
    assert canonical('https://cdn.example/video.mp4?Expires=123&sig=secret') == 'https://cdn.example/video.mp4'
    assert canonical('https://weibo.com/123/5344830698031714') == 'https://m.weibo.cn/detail/5344830698031714'
    assert canonical('https://haokan.baidu.com/v?vid=123&from=search') == 'https://haokan.baidu.com/v?vid=123'
    assert canonical('https://haokan.baidu.com/v?vid=123') != canonical('https://haokan.baidu.com/v?vid=456')
    assert positive(float('nan')) is None and positive(True) is None


def test_target_is_frozen_not_redoubled_from_latest_rate():
    result = summarize(*fixture())
    goal = dict(metric=result['metric'],window_days=7,long_threshold_seconds=900,
        baseline_fraction=dict(numerator=10,denominator=86),multiplier=2,baseline_as_of='2026-10-13')
    actual = apply_frozen_goal(result,goal)
    assert actual['target_rate'] == 20/86
    assert not actual['target_achieved']
    result['observed_success_rate'] = .5
    assert apply_frozen_goal(result,goal)['target_rate'] == 20/86
    changed = dict(goal,window_days=14)
    with pytest.raises(ValueError,match='different cohort'):
        apply_frozen_goal(result,changed)
