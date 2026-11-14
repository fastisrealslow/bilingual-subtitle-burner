import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'linyuan'))
from production_diagnostics import failure_category


def test_actual_ambiguity_report_is_not_a_duplicate_rejection():
    row = json.loads((ROOT / 'tests/fixtures/linyuan_actual_asr_ambiguity_diagnostic.json').read_text())
    assert row['old_category'] == 'duplicate'
    assert '重复' in row['reason'] and '企业销国' in row['reason']
    assert failure_category(row['reason']) == 'asr_transcript'


@pytest.mark.parametrize('detail', [
    '原话存在影响观点的识别歧义，须听辨或更换素材；原文问题：口吃和重复',
    '原话存在影响观点的识别歧义，须听辨或更换素材；原文问题：股票名称',
    '原话存在影响观点的识别歧义，须听辨或更换素材；原文问题：时间和数字',
])
def test_explicit_ambiguity_cause_precedes_incidental_keywords(detail):
    assert failure_category(detail) == 'asr_transcript'


@pytest.mark.parametrize('detail,expected', [
    ('与已发布内容文字指纹重复', 'duplicate'),
    ('与已经发布的母片时间段重叠，保留其他完整观点', 'duplicate'),
    ('原文中未找到连续候选；重复模型请求无助于恢复', 'selection'),
    ('原始ASR存在影响理解的疑点：重复两个字', 'asr_transcript'),
    ('Current user is in debt，原话存在影响观点的识别歧义', 'cloud_account_billing'),
    ('ASR强制对齐时序无效；原话存在影响观点的识别歧义', 'asr_alignment'),
])
def test_actual_duplicate_and_operational_categories_keep_their_priority(detail, expected):
    assert failure_category(detail) == expected
