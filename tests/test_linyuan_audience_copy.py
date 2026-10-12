import copy
from pathlib import Path
import sys
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan'))
from audience_copy import description, collection_copy, series_navigation
from batch_delivery import organize_interview_series


@pytest.mark.parametrize('claim', [
    '炒作小盘股能带动市场活跃性，但长期大盘更稳',
    '我没有买中石油，因为它不符合我的标准',
    '我认为位置应该不高，但还没有进入牛市',
    '这是2007年的访谈，不是今天的行情判断',
])
def test_known_wrapper_removed_without_changing_any_claim_or_hedge(claim):
    value=description('本段讨论：'+claim)
    assert value=='林园：'+claim
    assert description(value)==value


def test_preserve_ai_disclosure_credit_dates_and_legitimate_discussion():
    original='本段讨论：我还没有卖。\n来源：第一财经，2026-08-22\nAI生成插画\n未获得转载授权\n他说“本段讨论”这几个字。'
    result=description(original)
    assert result.splitlines()[1:]==original.splitlines()[1:]
    assert 'AI生成插画' in result and '未获得转载授权' in result


def test_old_cached_series_navigation_keeps_numbers_directory_and_release_uncertainty():
    value=('本段讨论：我没有买中石油\n\n'
           '同场访谈分集：第 2/3 集（各集独立成篇，发布以主页为准）\n'
           '分集目录：\n1. 林园：不加杠杆\n2. 林园：我没有买中石油\n3. 林园：等合适的价格')
    result=description(value)
    assert '同场访谈分集' not in result and '分集目录' not in result
    assert '第 2/3 集（已更新的片段见主页）' in result
    assert result.splitlines()[-3:]==value.splitlines()[-3:]
    assert '全部已发布' not in result and description(result)==result


def test_new_navigation_does_not_change_media_or_accepted_title():
    rows=[dict(title='林园：我没买中石油',desc='本段讨论：我没买中石油',
               source_sha256='a'*64,segments=[dict(start=0,end=130)]),
          dict(title='林园：不加杠杆',desc='本段讨论：不加杠杆',
               source_sha256='a'*64,segments=[dict(start=200,end=330)])]
    original=copy.deepcopy(rows)
    actual,manifest=organize_interview_series(rows,source_duration=1800,complete=True)
    assert rows==original and manifest['publication_status']=='not_verified'
    for old,new in zip(original,actual):
        assert new['title']==old['title'] and new['segments']==old['segments']
        assert '本段讨论' not in new['desc'] and '同场访谈分集' not in new['desc']


def test_collection_headline_never_truncates_conditions_or_promises_fake_firsthand_experience():
    title,desc=collection_copy('林园：'+('如果条件不变，' * 10)+'才有可能涨')
    assert title=='林园｜访谈片段' and '亲自' not in desc
    assert collection_copy('林园：我没有买中石油')[0]=='林园｜我没有买中石油'


def test_actual_fc_publish_boundary_cleans_old_artifact_without_editing_title():
    import importlib.util
    path=Path(__file__).resolve().parents[1]/'linyuan/fc/index.py'
    spec=importlib.util.spec_from_file_location('audience_fc',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    assert module.clean_publish_desc('本段讨论：我没有买中石油')=='林园：我没有买中石油'


def test_long_series_navigation_only_shows_adjacent_exact_approved_titles():
    episodes = [dict(episode_number=n, title=f'林园：原话第 {n} 段，不一定上涨') for n in range(1, 7)]
    middle = series_navigation(3, episodes)
    assert '第 3/6 集' in middle
    assert '上一段：' + episodes[1]['title'] in middle
    assert '下一段：' + episodes[3]['title'] in middle
    assert episodes[0]['title'] not in middle and episodes[5]['title'] not in middle
    assert '已更新的片段' in middle and '全部已发布' not in middle
    assert '上一段：' not in series_navigation(1, episodes)
    assert '下一段：' not in series_navigation(6, episodes)


def test_collection_topic_requires_all_actual_published_titles_not_first_episode_only():
    first = '林园：我没有买这家公司'
    title, desc = collection_copy(first, episode_titles=[first, '林园：研究企业生意'])
    assert title == '林园｜公司与生意'
    assert '2 段' in desc and '完整版' not in desc
    assert collection_copy(first, episode_titles=[first, '林园：人工智能是未来的赛道'])[0] != title
    assert collection_copy('林园：茅台', episode_titles=['林园：茅台', '林园：白酒'])[0] == '林园｜白酒生意'
    assert 'AI生成' in description('AI生成插画，来源：第一财经')


def test_cached_long_directory_migration_preserves_provenance_and_is_idempotent():
    old = ('本段讨论：我没买中石油\n'
           '同场访谈分集：第 3/6 集（各集独立成篇，发布以主页为准）\n分集目录：\n'
           + '\n'.join(f'{n}. 林园：原判断 {n}，不一定上涨' for n in range(1, 7))
           + '\n来源：第一财经，2020-04-12\nAI生成插画\n版权提示')
    cleaned = description(old)
    assert '上一段：林园：原判断 2，不一定上涨' in cleaned
    assert '下一段：林园：原判断 4，不一定上涨' in cleaned
    assert cleaned.endswith('来源：第一财经，2020-04-12\nAI生成插画\n版权提示')
    assert description(cleaned) == cleaned
    # An irregular directory may contain unrelated notes. Never reinterpret it.
    malformed = old.replace('2. 林园', '7. 林园')
    assert '这一组还有：' in description(malformed)
