"""Known duplicates must be skipped before a slow media fetch, not after it."""
import copy
from pathlib import Path
import sys
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan/fc'))
import index as f


def rows():
    entry=dict(slug='current',source_url='https://example.test/current',published_parts=0)
    fingerprints=dict(sha256='old-file',transcript_ngrams=[f'phrase{i}' for i in range(10)])
    parts=[dict(index=0,status='verified',render_mode='live_video_card',fingerprints=fingerprints),
           dict(index=1,status='verified',render_mode='live_video_card',fingerprints=dict(sha256='new-file'))]
    state=dict(dispatched=[entry],published=dict(previous=dict(bvid='BV-existing',ts=int(time.time()),
        title='old independent topic',source_url='https://example.test/previous',fingerprints=fingerprints)))
    record=dict(slug=entry['slug'],artifact_id=123,parts=parts)
    return entry,record,state


def test_current_history_skips_known_duplicate_and_selects_next_inspected_file_without_mutation():
    entry,record,state=rows();before=copy.deepcopy(state)
    assert f.inventory_part_index(entry,123,[record],{})==0  # Original mismatch.
    assert f.inventory_publication_error(record['parts'][0],entry,state)
    assert f.inventory_part_index(entry,123,[record],{},publication_state=state)==1
    assert state==before and entry['published_parts']==0


def test_only_known_duplicate_does_not_fall_back_to_rejected_index_zero():
    entry,record,state=rows();record['parts']=record['parts'][:1]
    assert f.inventory_part_index(entry,123,[record],{},publication_state=state) is None


def test_current_topic_cooling_is_shared_with_stock_counts_before_fetch():
    entry,record,state=rows()
    record['parts'][0]['fingerprints']={'sha256':'different-file'}
    record['parts'][0]['title']='林园：不要借钱追逐短期股价波动'
    record['parts'][1]['title']='林园：判断企业竞争力先看现金流'
    state['published']['previous']['title']=record['parts'][0]['title']
    assert f.inventory_part_index(entry,123,[record],{},publication_state=state)==1


def test_preflight_never_bypasses_known_source_restrictions():
    entry,record,state=rows()
    entry['source_url']='https://www.bilibili.com/video/BV1RJ411Q7Ki'
    assert f.inventory_part_index(entry,123,[record],{},publication_state=state) is None


def test_unknown_new_delivery_keeps_existing_full_postdownload_validation_path():
    entry,record,state=rows();entry['published_parts']=2
    assert f.inventory_part_index(entry,456,[record],{},publication_state=state)==2


def test_publisher_wires_preflight_only_for_normal_and_catchup_not_explicit_legacy_batches():
    code=(Path(__file__).resolve().parents[1]/'linyuan/fc/index.py').read_text()
    assert 'publication_state=st if not batch_slug or makeup_slot else None' in code
    assert 'and not inventory_publication_error(p,entry,st)' in code
    # Existing independent final checks stay in place; preflight is additive.
    assert 'find_content_duplicate(part.get("fingerprints") or {}, comparison_state)' in code
