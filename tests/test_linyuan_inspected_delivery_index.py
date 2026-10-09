"""A repaired delivery must not be shadowed by its older producer ZIP."""
import sys
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1] / 'linyuan'))
from fc import index as fc


def test_fresh_inspected_repair_replaces_producer_zip_without_changing_verdicts(monkeypatch):
    monkeypatch.setattr(fc,'inventory_record_current',lambda row:True)
    arts={'ly-1006-32e054':'old-zip'}
    ids={'ly-1006-32e054':11589227435}
    part=dict(status='verified',sha256='real-sha')
    record=dict(slug='ly-1006-32e054',artifact_id=11595619214,parts=[part])
    fc.apply_inspected_delivery_index(arts,ids,[record])
    assert ids['ly-1006-32e054']==11595619214
    assert arts['ly-1006-32e054'].endswith('/11595619214/zip')
    assert record['parts']==[part]


def test_expired_record_never_overwrites_existing_file(monkeypatch):
    monkeypatch.setattr(fc,'inventory_record_current',lambda row:False)
    arts={'source':'existing'}; ids={'source':1}
    fc.apply_inspected_delivery_index(arts,ids,[dict(slug='source',artifact_id=2)])
    assert arts=={'source':'existing'} and ids=={'source':1}


def test_rejected_present_file_is_not_promoted_or_lost(monkeypatch):
    monkeypatch.setattr(fc,'inventory_record_current',lambda row:True)
    part=dict(status='rejected',reason='真实画面不合格')
    record=dict(slug='source',artifact_id=2,parts=[part]); arts={}; ids={}
    fc.apply_inspected_delivery_index(arts,ids,[record])
    assert ids['source']==2 and part['status']=='rejected'
