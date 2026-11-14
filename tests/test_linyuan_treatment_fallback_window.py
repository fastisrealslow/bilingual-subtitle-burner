"""Late verified stories can fill their slot before bounded ordinary fallback."""
from copy import deepcopy
from datetime import datetime
from pathlib import Path
import sys
import pytest

sys.path[:0]=[str(Path(__file__).resolve().parents[1]/'linyuan/fc'),str(Path(__file__).resolve().parents[1]/'linyuan')]
import index as F
from test_linyuan_experiment_inventory_refresh import setup


def stamp(clock):
    return datetime.fromisoformat('2026-11-20T'+clock).replace(tzinfo=F.experiment.CST).timestamp()


def stock(wide=0,portrait=0):
    return dict(inventory_fresh=True,experiment_ready=dict(landscape=wide,portrait=portrait))


@pytest.mark.parametrize('clock,slot,deadline',[
    ('14:00:00',14,'15:40:00'),('15:39:59',14,'15:40:00'),
    ('15:40:00',14,None),('21:00:00',21,'21:40:00'),
    ('21:39:59',21,'21:40:00'),('21:40:00',21,None),
    ('10:00:00',10,None),('16:00:00',16,None)])
def test_wait_boundaries_and_existing_control_slots(clock,slot,deadline):
    now=stamp(clock)
    plan=F.experiment.production_assignment(now,f'2026-11-20 {slot:02d}')
    assert F.treatment_wait_until(stock(),plan,now)==(stamp(deadline) if deadline else None)


@pytest.mark.parametrize('hour,wide,portrait,wait',[
    (14,1,0,False),(14,0,1,True),(21,0,1,False),(21,1,0,False)])
def test_publish_verified_story_immediately_only_when_it_fits_slot(hour,wide,portrait,wait):
    now=stamp(f'{hour}:00:00');plan=F.experiment.production_assignment(now)
    assert bool(F.treatment_wait_until(stock(wide,portrait),plan,now)) is wait


@pytest.mark.parametrize('inventory',[{},dict(inventory_fresh=True),dict(inventory_fresh=False,experiment_ready={})])
def test_unknown_inventory_does_not_masquerade_as_verified_story_shortage(inventory):
    now=stamp('14:00:00')
    assert F.treatment_wait_until(inventory,F.experiment.production_assignment(now),now) is None


def test_automatic_publisher_returns_without_scanning_or_upload_and_reuses_backoff(monkeypatch):
    state,calls,now=setup(monkeypatch,14,'2026-11-20')
    before=deepcopy(state)
    monkeypatch.setattr(F,'source_inventory',lambda _:stock())
    expected=dict(published=0,waiting_for_treatment=1,
                  fallback_after=stamp('15:40:00'),inventory_refresh_requested=1)
    assert F.publish_handler()==expected
    assert F.publish_handler()=={**expected,'inventory_refresh_requested':0}
    assert len(calls)==1 and state.pop('inventory_refresh_requested_at')==now
    assert state==before


def test_catchup_keeps_slot_open_then_allows_existing_fallback(monkeypatch):
    now=stamp('14:00:00')
    state=dict(dispatched=[],published={},daily_publish=dict(date='2026-11-20',count=1,published_hours=[10]))
    monkeypatch.setattr(F,'source_inventory',lambda *a:stock())
    # No artifacts may even be ranked while the verified-stock window is open.
    class Unreadable(dict):
        def get(self,*args):raise AssertionError('fallback stock read too early')
    assert F.inventory_catchup_request(state,Unreadable(),now) is None
    import test_linyuan_publication_cover_priority as C
    record=C.record('ordinary',[C.part()])
    state['dispatched']=[dict(slug='ordinary',source_url='https://example.test')]
    monkeypatch.setattr(F.experiment,'rank',lambda *a:0)
    monkeypatch.setattr(F,'inventory_publication_error',lambda *a:None)
    result=F.inventory_catchup_request(state,dict(artifacts=[record]),stamp('15:40:00'))
    assert result['makeup_slot']=='2026-11-20 14' and result['batch_slug']=='ordinary'
