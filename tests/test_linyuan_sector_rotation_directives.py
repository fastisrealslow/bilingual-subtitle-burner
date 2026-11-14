import hashlib
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'linyuan'))
import title_rewrite as T


def test_actual_descriptive_rotation_cannot_become_a_prohibition():
    row = json.loads((ROOT / 'tests/fixtures/linyuan_actual_sector_rotation_directive.json').read_text())
    text = ''.join(c['text'] for c in row['cues'])
    assert hashlib.sha256(text.encode()).hexdigest() == row['transcript_sha256']
    assert all(row['old_proof']['review'][key] for key in T.CHECKS)
    assert '非绝对禁止' in row['old_proof']['review']['source_reading']['a_guest_answer']
    assert '买卖指令' in T.error(row['old_title'], row['old_proof'], text)
    item = dict(title=row['old_title'], cover_title=row['old_proof']['cover'],
                subject=row['old_proof']['subject'], evidence=row['old_proof']['evidence'])
    assert '买卖指令' in T._candidate_error(item, text, '林园', (), check_layout=False)


@pytest.mark.parametrize('copy,source', [
    ('别往小盘股上跑', '行情向小盘股蔓延也是一种市场特征。'),
    ('不要向周期性行业扎堆', '周期性行业开始活跃。'),
    ('必须往科技板块转', '科技板块今年表现比较好。'),
    ('赶紧朝消费赛道涌', '消费赛道出现了更多参与者。'),
    ('别往科技股跑', '别往医药股跑。'),
    ('别往小盘股跑', '我别往小盘股跑，这是我的选择。'),
    ('千万不要往基金里挤', '不建议往基金里挤。'),
    ('不要往小盘股跑', '应该往小盘股跑。'),
])
def test_unsupported_destination_direction_or_strength_rejects_each_surface(copy, source):
    assert T.unsupported_trade_directive_error('林园：' + copy, '行业正在轮动', source)
    assert T.unsupported_trade_directive_error('林园：行业正在轮动', copy, source)


@pytest.mark.parametrize('copy,source', [
    ('别往小盘股跑', '不要往小盘股跑。'),
    ('不要向周期性行业扎堆', '千万不要向周期性行业扎堆。'),
    ('必须往科技板块转', '必须往科技板块转。'),
    ('不建议往基金里挤', '不要往基金里挤。'),
    ('我不要往小盘股跑', '我不要往小盘股跑，这是个人选择。'),
    ('行情向小盘股蔓延', '行情向小盘股蔓延也是牛市特征。'),
    ('抓主流大市值', '现在就是抓主流的，抓一些大市值的东西。'),
    ('别往马路上跑', '我们在谈交通安全。'),
])
def test_explicit_advice_personal_choices_and_descriptions_continue_normal_review(copy, source):
    assert T.unsupported_trade_directive_error('林园：' + copy, copy, source) is None
