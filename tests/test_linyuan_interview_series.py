"""Serial navigation only includes independently accepted complete arguments."""
import copy
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'linyuan'))
from batch_delivery import organize_interview_series, archive_accepted, write_json


def rows():
    return [dict(source_sha256='a'*64, title=title, desc='已核验简介',
                 final=f'final_{i}.mp4', part=i,
                 segments=[dict(start=start, end=start+150)])
            for i, (title, start) in enumerate([('看企业的现金流', 600),
                                               ('如何判断供需关系', 100)], 1)]


def build(data, **kw):
    return organize_interview_series(data, source_duration=2400,
                                     complete=True, **kw)


def test_original_time_order_and_unmodified_title_and_media_proofs():
    data=rows(); original=copy.deepcopy(data)
    enriched, manifest=build(data)
    assert data == original
    assert [e['source_start'] for e in manifest['episodes']] == [100,600]
    assert [r['interview_series']['episode_number'] for r in enriched] == [2,1]
    assert enriched[0]['title'] == original[0]['title']
    assert enriched[0]['segments'] == original[0]['segments']
    assert '发布以主页为准' in enriched[0]['desc']
    assert enriched[0]['interview_series']['previous_title'] == data[1]['title']
    assert not manifest['platform_collection_created']
    assert manifest['publication_status'] == 'not_verified'


@pytest.mark.parametrize('args', [dict(complete=False), dict(selective_retry=True),
                                 dict(source_duration=500)])
def test_partial_repairs_and_short_sources_never_announce_series(args):
    data=rows()
    options=dict(source_duration=2400,complete=True);options.update(args)
    assert organize_interview_series(data,**options) == (data,None)


def test_plan_identity_stable_for_order_changes_but_not_different_ranges():
    data=rows(); _, a=build(data); _, b=build(list(reversed(data)))
    assert a['plan_id'] == b['plan_id']
    data[0]['segments'][0]['end'] -= 5
    _, c=build(data)
    assert a['series_id'] == c['series_id']
    assert a['plan_id'] != c['plan_id']


@pytest.mark.parametrize('fault', ['overlap','wrong_mother','nan','duplicate_title',
    'multi_answer','missing_segments','oversize_desc','newline_title','out_of_bounds'])
def test_unprovable_serial_plan_leaves_accepted_clips_unchanged(fault):
    data=rows()
    if fault=='overlap':data[0]['segments'][0]['start']=200
    if fault=='wrong_mother':data[0]['source_sha256']='b'*64
    if fault=='nan':data[0]['segments'][0]['end']=float('nan')
    if fault=='duplicate_title':data[0]['title']=data[1]['title']
    if fault=='multi_answer':data[0]['segments'].append(dict(start=900,end=1050))
    if fault=='missing_segments':data[0].pop('segments')
    if fault=='oversize_desc':data[0]['desc']='文'*1999
    if fault=='newline_title':data[0]['title']='标题\n第二集'
    if fault=='out_of_bounds':data[0]['segments'][0]['end']=3000
    assert build(data) == (data,None)


def test_full_interview_is_not_a_duplicate_numbered_episode():
    data=rows()+[dict(content_type='full_interview', title='完整访谈')]
    enriched, manifest=build(data)
    assert manifest['episode_count']==2
    assert enriched[-1]==data[-1]


def test_one_good_part_not_enough_for_series():
    data=rows()[:1]
    assert build(data)==(data,None)


def test_series_manifest_delivered_only_with_accepted_parts(tmp_path):
    data=rows()
    for i,r in enumerate(data,1):
        for key,extension in [('final','mp4'),('cover','jpg'),('preview_30s','mp4'),
                              ('contact_sheet_6','jpg')]:
            r[key]=f'{key}_{i}.{extension}'
            (tmp_path/r[key]).write_bytes(b'accepted')
    data,manifest=build(data)
    write_json(tmp_path/'meta.json',data)
    write_json(tmp_path/'interview_series.json',manifest)
    (tmp_path/'rejected.mp4').write_bytes(b'rejected')
    assert archive_accepted(tmp_path,'interview')==2
    delivered=tmp_path/'_accepted'
    assert json.loads((delivered/'interview_series.json').read_text())==manifest
    assert not (delivered/'rejected.mp4').exists()
    (tmp_path/'interview_series.json').unlink()
    with pytest.raises(ValueError,match='series manifest'):
        archive_accepted(tmp_path,'interview')
