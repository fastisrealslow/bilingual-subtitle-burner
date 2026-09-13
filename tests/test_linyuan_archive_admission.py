import copy
from pathlib import Path
import sys
sys.path[:0]=[str(Path(__file__).resolve().parents[1]/'linyuan/fc'),str(Path(__file__).resolve().parents[1]/'linyuan')]
import index as fc


def item():
    return dict(id='bilibili_search:BV19P4y1m77K:p46',source='bilibili_collection',
        title='林园 P46 2020.12.25《红周刊投资峰会》',author='我爱A股',
        url='https://www.bilibili.com/video/BV19P4y1m77K?p=46',
        extra=dict(collection_title='林园（2005～2022）',page=46,cid=46,duration=2357,
            direct_dispatch=True,source_role='mother_candidate',metadata_status='known_duration'))


def test_real_archive_programme_title_is_admitted_from_verified_parent_metadata():
    row=item()
    assert not fc.title_has_target_speaker(row['title'])
    assert len(fc.pick([row],dict(dispatched=[],published={},rejected=[]),4))==1


def test_unknown_author_or_reference_metadata_cannot_open_archive_admission():
    for field,value in [('direct_dispatch',False),('source_role','reference'),('cid',None),('collection_title','其他人物合集')]:
        row=item();row['extra'][field]=value
        assert not fc.item_has_target_speaker(row,row['extra'])
    row=item();row['author']=''
    assert not fc.item_has_target_speaker(row,row['extra'])


def test_already_dispatched_archive_page_remains_blocked():
    row=item()
    state=dict(dispatched=[dict(key=row['id'],slug='old')],published={},rejected=[])
    assert not fc.pick([row],state,4)
