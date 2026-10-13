import copy
from pathlib import Path
import sys

import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'linyuan/fc'),str(ROOT/'linyuan')]
import index as fc


def row():
    return dict(id='bilibili_search:BV15au86KERp',title='林园 P1 studio_video_1786109908572',
        source='bilibili_search',author='唐晶晶的价值观',url='https://www.bilibili.com/video/BV15au86KERp',
        extra=dict(bvid='BV15au86KERp',page=1,cid=40691764475,duration=3219,
            collection_title='《林园》2026年8月7日最新采访',source_role='mother_candidate',
            direct_dispatch=True,metadata_status='known_duration',no_reprint=0))


def state():return dict(dispatched=[],published={},rejected=[])


@pytest.mark.parametrize('source',['bilibili_search','bilibili_series','reference_origin_search'])
def test_real_imported_page_uses_exact_parent_evidence_not_its_filename(source):
    item=row();item['source']=source;before=copy.deepcopy(item)
    assert not fc.title_has_target_speaker(item['title'])
    assert fc.item_has_target_speaker(item,item['extra'])
    assert len(fc.source_admission_audit([item],state())['candidates'])==1
    assert item==before


@pytest.mark.parametrize('change',[
    lambda r:r.update(source='weibo_search'),
    lambda r:r.update(id='different-import'),
    lambda r:r.update(url='https://www.bilibili.com/video/BV15au86KERp?p=2'),
    lambda r:r.update(url='https://example.com/?url=https://www.bilibili.com/video/BV15au86KERp'),
    lambda r:r.update(title='别人谈论林园'),
    lambda r:r['extra'].update(bvid='other'),
    lambda r:r['extra'].update(page=True),
    lambda r:r['extra'].update(cid=None),
    lambda r:r['extra'].update(source_role='reference'),
    lambda r:r['extra'].update(direct_dispatch=False),
    lambda r:r['extra'].update(metadata_status='needs_author_verification'),
    lambda r:r['extra'].update(collection_title='林园朋友谈投资'),
    lambda r:r['extra'].update(collection_title='AI访谈林园：投资应该怎么做'),
])
def test_unbound_third_party_or_unverified_metadata_never_opens_speaker_gate(change):
    item=row();change(item)
    assert not fc.item_has_target_speaker(item,item['extra'])


def test_explicit_rights_and_duplicate_gates_remain_effective():
    item=row();item['extra']['no_reprint']=1
    assert fc.source_admission_audit([item],state())['candidate_count']==0
    item['extra']['no_reprint']=0;st=state();st['dispatched']=[{'key':item['id'],'slug':'failed','failed':True}]
    assert fc.source_admission_audit([item],st)['candidate_count']==0


def test_true_collection_parent_can_identify_interview_subject_after_date_prefix():
    item=row();item.update(source='bilibili_collection')
    item['extra']['collection_title']='10月14日林园最新访谈：带你看懂A股大反转'
    assert fc.item_has_target_speaker(item,item['extra'])
