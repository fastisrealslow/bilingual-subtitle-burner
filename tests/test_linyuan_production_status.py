"""Regressions from the Sep 16 log: repeated retries and 279-hour ghost jobs."""
import sys
from pathlib import Path
from datetime import datetime, timezone
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'linyuan'))
import production_status as status
import source_supply

NOW = 1789531200


def run(slug='mother', id=101, result='success', state='completed', ts=NOW-30):
    stamp = datetime.fromtimestamp(ts, timezone.utc).isoformat()
    return dict(id=id, display_title='中文源出片 · '+slug, status=state, conclusion=result,
                created_at=stamp, updated_at=stamp, html_url=f'https://github.com/example/runs/{id}')


def entry(**kwargs):
    return dict(slug='mother', ts=NOW-100, production_rules_version=status.fc.PRODUCTION_RULES_VERSION, **kwargs)


def state(row):
    return dict(dispatched=[row], published={})


def inventory(parts):
    return dict(updated_at=NOW, quality_gate_version=status.fc.QUALITY_GATE_VERSION,
                editorial_policy_version=status.fc.editorial.VERSION,
                artifacts=[dict(slug='mother', artifact_id=55, parts=parts)])


def test_nine_retry_rows_have_one_terminal_state_even_if_history_is_unordered():
    rows=[dict(slug='ly-0906-12a951', ts=i) for i in range(8)]
    latest=dict(slug='ly-0906-12a951', ts=9, failed=True, last_error='素材质量门禁未通过')
    result=status.build(dict(dispatched=[latest]+rows, published={}), {}, {}, NOW)
    assert len(result['tasks']) == 1
    assert result['tasks'][0]['status'] == 'failed'
    assert result['tasks'][0]['history_count'] == 9


def test_paused_partial_old_batch_is_never_a_stuck_production():
    row=dict(slug='ly-0904-f47739', ts=NOW-279*3600, published_parts=1, parts_total=2)
    s=state(row);s['published'][row['slug']]=dict(parts_total=2, bvid='BVexisting')
    assert status.classify(row,s,{},run(slug=row['slug']),NOW)[0] == 'paused'


def test_success_does_not_claim_inspected_or_publishable_stock():
    e=entry()
    assert status.classify(e,state(e),{},run(),NOW)[0] == 'awaiting_validation'


def test_verified_full_is_reserved_not_stuck(monkeypatch):
    e=entry(weekly_full_week='2026-W38')
    p=dict(index=0,status='verified',content_type='full_interview',render_mode='crop_delogo')
    monkeypatch.setattr(status.fc,'inventory_publication_error',lambda *a:None)
    assert status.classify(e,state(e),inventory([p]),run(),NOW)[0] == 'reserved'
    old=inventory([p]);old['updated_at']=NOW-4*3600
    assert status.classify(e,state(e),old,run(),NOW)[0] == 'awaiting_validation'


def test_audio_card_is_not_reported_as_ready_under_live_only_policy(monkeypatch):
    e=entry()
    monkeypatch.setattr(status.fc,'inventory_publication_error',lambda *a:None)
    assert status.classify(e,state(e),inventory([dict(index=0,status='verified',render_mode='audio_card')]),run(),NOW)[0] == 'excluded'


def test_processed_noncontiguous_parts_cannot_become_ready_again(monkeypatch):
    e=entry(published_parts=1,processed_part_indices=[2],parts_total=3)
    s=state(e);s['published']['mother']=dict(parts_total=3)
    parts=[dict(index=i,status='verified') for i in (0,2)]
    parts.append(dict(index=1,status='rejected',reason='原字幕污染'))
    monkeypatch.setattr(status.fc,'inventory_publication_error',lambda *a:None)
    assert status.classify(e,s,inventory(parts),run(),NOW)[0] == 'validation_failed'
    e['processed_part_indices']=[1,2]
    assert status.classify(e,s,inventory(parts),run(),NOW)[0] == 'complete'


def test_single_published_receipt_needs_no_dispatch_progress_flag():
    e=entry();s=state(e);s['published']['mother']=dict(bvid='BVdone',parts_total=1)
    assert status.classify(e,s,{},run(state='in_progress'),NOW)[0] == 'complete'


def test_actual_active_run_beats_old_failed_flag_but_not_explicit_pause():
    e=entry(failed=True)
    assert status.classify(e,state(e),{},run(state='in_progress',result=None),NOW)[0] == 'running'


def test_recovery_intent_is_not_overridden_by_previous_failure():
    e=entry(source_check_retry_after=NOW-1)
    assert status.classify(e,state(e),{},run(ts=NOW-3600,result='failure'),NOW)[0] == 'retry_queued'


def test_old_missing_evidence_is_unknown_not_running_or_failed():
    e=entry();e['ts']=NOW-279*3600
    assert status.classify(e,state(e),{},None,NOW)[0] == 'unknown'


def test_backfill_older_run_then_cache_it_without_repeating_history_scan():
    e=entry();e['ts']=NOW-86400
    calls=[]
    page1=[run(slug='unrelated'+str(i),id=200+i) for i in range(100)]
    def api(path):
        calls.append(path)
        return dict(workflow_runs=page1 if path.endswith('&page=1') else [run(ts=NOW-86300)])
    runs=status.collect_runs(state(e),api)
    assert runs['mother']['id']==101 and len(calls)==2
    calls.clear()
    status.collect_runs(state(e),api,dict(runs=runs))
    assert len(calls)==1


def test_unavailable_actions_does_not_replace_snapshot_with_empty_queue():
    def unavailable(path):raise RuntimeError('temporary network error')
    with pytest.raises(RuntimeError):status.collect_runs(state(entry()),unavailable)


def test_exact_old_run_recovers_delivery_beyond_recent_artifact_pages():
    recent=[dict(name='editorial-unrelated',id=i) for i in range(100)]
    delivery=dict(name='deliver-mother',id=55,expired=False)
    expired=dict(name='deliver-mother',id=56,expired=True)
    calls=[]
    def api(path):
        calls.append(path)
        return dict(artifacts=[delivery,expired] if '/runs/101/' in path else recent)
    found=source_supply.find_deliveries({'mother':entry()},api,{'mother':run()})
    assert found=={'mother':delivery}
    assert calls[-1]=='actions/runs/101/artifacts'


def test_failed_partial_batch_can_still_have_real_delivery():
    delivery=dict(name='deliver-mother',id=55,expired=False)
    def api(path):return dict(artifacts=[delivery] if '/runs/' in path else [])
    assert source_supply.find_deliveries({'mother':entry()},api,{'mother':run(result='failure')})['mother']==delivery
