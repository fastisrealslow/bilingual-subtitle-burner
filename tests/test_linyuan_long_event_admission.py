"""Real long-event catalogue titles must reach media inspection, never approval."""
from datetime import datetime
import json
from pathlib import Path
import sys

import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'linyuan'))
from fc import index as F
import stage_and_dispatch as local
from monitor_v2 import BilibiliSearchSource
from verified_seed_snapshot import items


def refreshed():
    return json.loads((ROOT/'linyuan/operations/long-event-metadata-recheck-20261028.json').read_text())['rows']


def test_five_real_event_titles_reach_normal_admission_without_media_approval(monkeypatch):
    rows=refreshed()
    now=max(datetime.fromisoformat(r['checked_at']).timestamp() for r in rows)+1
    monkeypatch.setattr(F.time,'time',lambda:now)
    candidates=[item for row in rows for item in items(row,now)]
    assert len(candidates)==5
    assert all(F.title_has_target_speaker(row['title']) and local.title_has_target_speaker(row['title'])
               and BilibiliSearchSource.FULL_TITLE_PAT.search(row['title']) for row in rows)
    state=dict(dispatched=[],published={},rejected=[],pending_retry=[])
    accepted=F.pick(candidates,state,len(candidates))
    assert len(accepted)==5
    for candidate in accepted:
        extra=candidate['extra']
        assert extra['media_verified'] is extra['editorial_approved'] is False
        assert 900<=extra['duration']<=5400
    assert state['published']=={}


@pytest.mark.parametrize('title',[
    '林园粉丝讲座：如何选股','如何看林园投资者交流活动',
    '林园基金年度报告全集','林园好友讲座：我自己的投资经历',
])
def test_new_event_labels_still_reject_third_party_and_fund_commentary(title):
    assert not F.title_has_target_speaker(title)


@pytest.mark.parametrize('change',[
    dict(no_reprint=1),dict(direct_dispatch=False,source_role='catalog_only'),
    dict(media_verified=True),dict(editorial_approved=True),
])
def test_event_label_cannot_convert_restrictions_or_old_approval_into_new_evidence(change):
    row=refreshed()[0];row.update(change)
    now=datetime.fromisoformat(row['checked_at']).timestamp()+1
    assert items(row,now)==[]
