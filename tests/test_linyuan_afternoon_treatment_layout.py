"""An unfinished wide worker cannot replace today's actual afternoon draft."""
from datetime import datetime
import pytest
from test_linyuan_experiment_duration_probe import F,dispatch_env,candidate,NOW


def stamp(clock):
    return datetime.fromisoformat('2026-11-15T'+clock).replace(tzinfo=F.experiment.CST).timestamp()


def stock(wide=0):
    return dict(inventory_fresh=True,experiment_ready=dict(landscape=wide,portrait=0))


@pytest.mark.parametrize('clock',['00:01:00','10:54:00','14:00:00','15:39:59'])
def test_today_uncovered_afternoon_beats_larger_portrait_reserve(clock):
    assert F.treatment_admission_layout(stock(),{},dict(landscape=1,portrait=2),now=stamp(clock))=='landscape'


def test_real_wide_stock_keeps_existing_balanced_reserve_planning():
    assert F.treatment_admission_layout(stock(1),{},dict(landscape=1,portrait=2),now=stamp('10:00:00'))=='portrait'


def test_running_workers_do_not_claim_to_be_actual_stock_and_wide_can_fill_evening_gap():
    needs=dict(landscape=0,portrait=2)
    result=F.treatment_admission_layout(stock(),{},needs,now=stamp('10:00:00'))
    assert result=='landscape'
    F.experiment.consume_production_need(needs,result)
    assert needs==dict(landscape=0,portrait=1)


@pytest.mark.parametrize('options',[dict(full_week=True),dict(duration_probe=True),dict(urgent_wide_reserved=True)])
def test_existing_weekly_probe_and_one_urgent_wide_per_batch_rules(options):
    assert F.treatment_admission_layout(stock(),{},dict(landscape=1,portrait=2),now=stamp('10:00:00'),**options)=='portrait'


def test_published_slot_is_not_replanned_but_required_afternoon_remains_open():
    state=dict(daily_publish=dict(date='2026-11-15',count=2,published_hours=[10,14]))
    assert F.treatment_admission_layout(stock(),state,dict(landscape=1,portrait=2),now=stamp('14:30:00'))=='portrait'
    assert F.treatment_admission_layout(stock(),{},dict(landscape=1,portrait=2),now=stamp('15:40:00'))=='landscape'
    assert F.treatment_admission_layout(stock(),{},dict(landscape=1,portrait=2),now=stamp('23:30:00'))=='landscape'


@pytest.mark.parametrize('inventory',[dict(inventory_fresh=True),dict(inventory_fresh=False,experiment_ready={}),
    dict(inventory_fresh=True,experiment_ready=dict(landscape=None))])
def test_unknown_or_stale_stock_is_not_an_empty_wide_shelf(inventory):
    assert F.treatment_admission_layout(inventory,{},dict(landscape=1,portrait=2),now=stamp('10:00:00'))=='portrait'


def test_actual_admission_keeps_one_new_wide_before_balancing_portrait(dispatch_env,monkeypatch):
    state,candidates,posts,runs=dispatch_env
    inventory={**stock(), 'verified_landscape':100,'verified_portrait':37,'daily_mix_usable':137}
    monkeypatch.setattr(F,'source_inventory',lambda *a:inventory)
    # One actual wide producer is still running, leaving W=1/P=2 planning gaps.
    state['dispatched'].append(dict(slug='unfinished-wide',editorial_profile=F.experiment.TREATMENT,
                                   output_layout='landscape',ts=NOW))
    runs.append(dict(id=123,status='in_progress',display_title='source · unfinished-wide'))
    candidates.extend([candidate(1,4502),candidate(2,1800)])
    assert F._dispatch_admitted({'execution_backend':'github'})['dispatched']==2
    assert [p['output_layout'] for p in posts]==['landscape','portrait']
    assert all(p['editorial_profile']==F.experiment.TREATMENT for p in posts)
    assert state['dispatched'][0]['output_layout']=='landscape' and not state['published']
