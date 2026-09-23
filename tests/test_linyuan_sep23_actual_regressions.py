"""Observed artifact defects, including counterexamples to over-broad fixes."""
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'linyuan'))
import headline_policy as H
import title_rewrite as T
import production_diagnostics as D


@pytest.mark.parametrize('cover,expected', [
    ('林园听说光伏污染大', '听说光伏能源污染大'),
    ('林园：听说光伏能源污染大', '听说光伏能源污染大'),
])
def test_cover_removes_reporting_name_before_repair_and_review(cover, expected):
    source='我听说光伏能源污染很大，所以我们没有参与。'
    result=T.bind_candidate(dict(title='林园：听说光伏能源污染大',cover_title=cover),
        dict(evidence_ids=[0]), [source], {'光伏能源':[0]})
    assert result['cover_title']==expected
    assert result['evidence']==[source]
    assert not T._candidate_error(result,source,'林园',(),check_layout=False)


def test_name_inside_company_is_not_removed():
    source='林园投资公司重点选择医药企业。'
    result=T.bind_candidate(dict(title='林园：林园投资公司重点选择医药企业',
        cover_title='林园投资公司重点选择医药企业'),dict(evidence_ids=[0]),
        [source], {'医药企业':[0]})
    assert result['cover_title']=='林园投资公司重点选择医药企业'


def test_actual_source17_caption_does_not_split_speaker_honorific():
    from presentation import wrap_words
    text='嗯那下面这个问题是林总光伏能源你怎么看呢？'
    lines=wrap_words(text,13)
    assert ''.join(lines)==text
    assert any('林总' in line for line in lines)


@pytest.mark.parametrize('copy', ['就我们这一代人，大概再能活个三十多岁',
                                '我们还能活二十岁', '我们又活十岁'])
def test_remaining_lifetime_cannot_be_promoted_as_age(copy):
    assert H.verbal_fragment(copy)
    assert T.copy_fragment(copy)
    assert not H.complete(copy)


@pytest.mark.parametrize('copy', ['我们大概还能活三十多年', '我们希望活到九十岁',
                                '我现在已经活了六十岁'])
def test_duration_and_attained_age_not_confused(copy):
    assert not H.verbal_fragment(copy)


@pytest.mark.parametrize('small,category', [(0,'identity'),(300,'resolution')])
def test_tracker_failure_distinguishes_actual_small_faces_from_threshold_label(small,category):
    reason=f'动态取景剩余帧即使全部匹配也达不到80%：已匹配510/921，总帧数2052；身份匹配但人脸短边不足96px的帧数{small}'
    assert D.failure_category(reason)==category
    assert D.failure_category('源片短边不足480')=='resolution'


def test_real_batch_failures_do_not_hide_title_retries_or_missing_publication_ranges():
    assert D.failure_category('标题文案待重试：未提炼出有原文支撑的完整观点标题')=='title'
    assert D.failure_category('同源历史缺少可核对的起止段，不能确认是未用内容')=='publication_history'
    assert D.failure_category('同一来源URL的母片内容哈希已改变，须核对旧段对应关系')=='publication_history'
    assert D.failure_category('与已经发布的母片时间段重叠，保留其他完整观点')=='duplicate'
    assert D.failure_category('原文中未找到连续候选；重复模型请求无助于恢复')=='selection'
