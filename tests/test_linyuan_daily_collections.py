import sys
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'linyuan'))
from platform_collections import plans, published_day


def stamp(value):
    return datetime.fromisoformat(value).replace(tzinfo=timezone.utc).timestamp()


def test_daily_groups_use_beijing_publication_day_not_recording_day_or_duration():
    state={'dispatched':[{'slug':'old','ts':1,'title':'林园2020.12.14访谈'}],
           'published':{'old':{'source_url':'https://example.com/old', 'parts':[
               {'bvid':'BVshort','title':'林园：短片','part_index':0,
                'ts':stamp('2026-10-14T16:05:00'),'duration_sec':70},
               {'bvid':'BVlong','title':'林园：长片','part_index':1,
                'ts':stamp('2026-10-15T03:00:00'),'duration_sec':600}]}}}
    rows=plans(state)
    assert len(rows)==1 and rows[0]['title']=='林园｜2026-10-15 更新'
    assert rows[0]['bvids']==['BVshort','BVlong']
    assert '2020-12-14（待核实）' in rows[0]['description']
    assert '不是讲话日期' in rows[0]['description']
    key=rows[0]['key']
    state['published']['old']['parts'].append({'bvid':'BVnext','title':'林园：新片',
        'part_index':2,'ts':stamp('2026-10-15T08:00:00')})
    assert plans(state)[0]['key']==key


def test_existing_same_source_collections_are_not_reclassified():
    state={'published':{'old':{'source_url':'https://example.com/old','parts':[
        {'bvid':'BVone','part_index':0,'ts':stamp('2026-10-14T04:00:00')},
        {'bvid':'BVtwo','part_index':1,'ts':stamp('2026-10-14T08:00:00')}]}}}
    assert plans(state)[0]['slug']=='old'
    assert published_day({'ts':True}) is None
    assert published_day({'ts':float('nan')}) is None


def test_unpublished_and_one_video_days_never_create_empty_collections():
    state={'published':{'x':{'source_url':'https://example.com','parts':[
        {'bvid':'BVone','part_index':0,'ts':stamp('2026-10-15T04:00:00')},
        {'bvid':'BVtwo','status':'skipped','part_index':1,'ts':stamp('2026-10-15T08:00:00')}]}}}
    assert plans(state)==[]
