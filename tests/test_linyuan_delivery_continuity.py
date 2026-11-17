"""Ordinary stock coverage must not masquerade as the required 4+2 supply."""
from copy import deepcopy
import pytest
from test_linyuan_production_status import status,inventory,NOW


def projection(monkeypatch,experiment,*,stock=140,stale=False):
    inv=inventory([])
    inv['inventory']=dict(daily_mix_usable=stock,verified_portrait=30 if stock else 0,
                          verified_landscape=stock-30 if stock else 0)
    if stale:inv['updated_at']=NOW-4*3600
    state=dict(dispatched=[],published={});before=deepcopy(state)
    monkeypatch.setattr(status.fc.experiment,'operational_status',lambda *a:experiment)
    result=status.build(state,inv,{},NOW)
    assert state==before
    return result['continuity']


def test_real_140_control_stock_and_zero_treatments_cannot_report_covered(monkeypatch):
    experiment=dict(active=True,status='needs_attention',alert='daily_treatment_gap',
        daily_requirement=dict(active=True,gap_by_now=2,stable_7_complete_days=False))
    c=projection(monkeypatch,experiment)
    assert c['normal_stock_status']=='covered' and c['verified_daily_stock']==140
    assert c['status']=='at_risk' and c['alert']=='daily_treatment_gap'
    assert c['quality_gates_relaxed'] is False


def test_today_met_does_not_claim_seven_days_of_stability(monkeypatch):
    c=projection(monkeypatch,dict(active=True,status='ready',
        daily_requirement=dict(active=True,gap_by_now=0,stable_7_complete_days=False)))
    assert c['status']=='at_risk' and c['alert']=='daily_delivery_not_yet_stable'


def test_healthy_stable_supply_is_covered_without_a_permanent_alarm(monkeypatch):
    c=projection(monkeypatch,dict(active=True,status='ready',
        daily_requirement=dict(active=True,gap_by_now=0,stable_7_complete_days=True)))
    assert c['normal_stock_status']==c['status']=='covered' and 'alert' not in c


@pytest.mark.parametrize('change',[dict(stale=True),dict(stock=0)])
def test_missing_normal_supply_keeps_its_stronger_unknown_or_stockout(monkeypatch,change):
    c=projection(monkeypatch,dict(active=True,status='needs_attention',alert='daily_treatment_gap'),**change)
    assert c['status']==('unknown' if change.get('stale') else 'stockout')


def test_inactive_experiment_does_not_change_normal_stock_coverage(monkeypatch):
    c=projection(monkeypatch,dict(active=False,status='inactive'))
    assert c['status']=='covered'
