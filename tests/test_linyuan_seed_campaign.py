import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'linyuan'))
from seed_campaign import census, verify_seed

BVID = 'BV1NpBFYkEKx'


def metadata(**updates):
    data = dict(bvid=BVID, title='林园完整访谈', owner=dict(name='真实上传者', mid=123),
                pubdate=1700000000, pages=[dict(page=1, cid=100, duration=600, part='完整访谈')])
    data.update(updates)
    return json.dumps(dict(code=0, data=data))


def test_live_metadata_must_match_id_author_and_real_page_duration():
    row = verify_seed(dict(bvid=BVID, title='搜索摘要不是实际标题'), lambda *a, **kw: metadata())
    assert row['status'] == 'verified_metadata'
    assert row['observed_author'] == '真实上传者'
    assert row['title'] == '林园完整访谈'
    assert row['pages'][0]['cid'] == 100
    assert not row['media_verified'] and not row['editorial_approved']
    assert row['rights_status'] == 'not_determined'


@pytest.mark.parametrize('updates', [dict(bvid='BV1wrong0000'), dict(title='动物园林园新闻'),
    dict(owner=dict(name='园园滚雪球')), dict(owner=dict(name='')),
    dict(title='林园会议录音版'), dict(pages=[dict(page=1, cid=100, duration=119)]),
    dict(pages=[dict(page=1, cid=100, duration=5401)])])
def test_unverified_or_unusable_leads_do_not_become_seed_evidence(updates):
    assert verify_seed(dict(bvid=BVID), lambda *a, **kw: metadata(**updates))['status'] == 'not_verified'


def test_census_excludes_reference_directory_and_duplicate_collection_roots(tmp_path):
    for name, key, values in [('douyin', 'urls', ['a', 'a']), ('haokan', 'vids', ['b']),
        ('netease', 'vcodes', []), ('yicai', 'ids', ['123456']),
        ('weibo', 'urls', ['https://weibo.com/2/detail/5331066086756985',
                          'https://m.weibo.cn/detail/5331066086756985'])]:
        (tmp_path / (name + '_seeds.json')).write_text(json.dumps({key: values}))
    (tmp_path / 'up_videos.json').write_text(json.dumps({'reference': {}, 'reference2': {}}))
    (tmp_path / 'monitor_v2_config.json').write_text(json.dumps([dict(type='bilibili_collection',
        seeds=[dict(bvid=BVID)], seeds_file='bilibili_seeds.json')]))
    (tmp_path / 'bilibili_seeds.json').write_text(json.dumps(dict(seeds=[dict(bvid=BVID), dict(bvid='BV1diff00000')])))
    result = census(tmp_path)
    assert result['total'] == 6 and result['counts']['bilibili'] == 2
    assert result['reference_only'] == 2
