"""Dedicated landscape slot, supply deficit and evidence-backed publication tags."""
from datetime import datetime, timezone
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'linyuan/fc'))
import index as fc
import catchup_from_inventory as catchup


def stamp(hour):
    return datetime(2026,9,12,hour-8,tzinfo=timezone.utc).timestamp()


def test_landscape_is_reserved_for_added_slot_and_not_consumed_at_ten():
    wide=dict(resolution=dict(width=1280,height=720),render_mode='live_video_card')
    portrait=dict(resolution=dict(width=720,height=1280),render_mode='live_video_card')
    assert fc.MAX_PUBLISH_PER_DAY==4
    for hour in (10,14,16,21):
        assert fc.is_regular_publish_hour(stamp(hour))
        assert fc.content_fits_slot(wide,now=stamp(hour)) == (hour==14)
        assert fc.content_fits_slot(portrait,now=stamp(hour)) == (hour!=14)
    assert not fc.content_fits_slot({},now=stamp(14))
    assert not fc.content_fits_slot({**wide,'render_mode':'audio_card'},now=stamp(14))


def test_inventory_selects_later_wide_part_without_processing_portrait():
    entry=dict(slug='stock',published_parts=0)
    parts=[dict(index=i,status='verified',render_mode='live_video_card',
                resolution=dict(width=w,height=720)) for i,w in enumerate((480,1280))]
    records=[dict(slug='stock',artifact_id=123,parts=parts)]
    assert fc.inventory_part_index(entry,123,records,{},now=stamp(14))==1
    assert fc.inventory_part_index(entry,123,records,{},now=stamp(10))==0
    assert entry['published_parts']==0
    fc.mark_part_processed(entry,1)
    assert fc.inventory_part_index(entry,123,records,{},now=stamp(14)) is None


def test_fourth_slot_and_existing_receipt_prevent_duplicate_post():
    state=dict(daily_publish=dict(date='2026-09-12',count=1,published_hours=[10]))
    assert fc.catchup_deficit(state,stamp(14))==1
    state['daily_publish'].update(count=2,published_hours=[10,14])
    assert fc.catchup_deficit(state,stamp(14))==0
    state['daily_publish'].update(count=4)
    assert fc.catchup_deficit(state,stamp(21))==0


def test_full_general_reserve_still_replenishes_missing_landscape(monkeypatch):
    monkeypatch.setattr(fc,'catchup_deficit',lambda state:0)
    stock=dict(inventory_fresh=True,daily_mix_usable=12,verified_landscape=0)
    assert catchup.inventory_action({},stock)=='dispatch-source-inventory'
    stock['verified_landscape']=2
    assert catchup.inventory_action({},stock) is None


def test_tags_follow_actual_clip_not_other_source_topics():
    text='这个时候只需要考虑财务，看企业的现金流。我们买的时候没有想着去卖出。'
    tags=fc.editorial.publication_tags(text,existing=['林园','价值投资','茅台','财务'])
    assert {'财务分析','长期持有','企业经营','现金流'} <= set(tags)
    assert '茅台' not in tags
    assert len(tags)<=8 and len(set(tags))==len(tags)
    assert '完整访谈' in fc.editorial.publication_tags(text,content_type='full_interview')


def test_no_audio_card_after_increasing_daily_quota():
    assert fc.daily_mix_error(dict(render_mode='audio_card'),dict(live_video_count=3))
