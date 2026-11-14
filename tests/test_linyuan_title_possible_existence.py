import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'linyuan'))
import title_possible_existence as E
import title_rewrite as T


def test_actual_published_file_old_approval_is_rechecked():
    actual = json.loads((ROOT / 'tests/fixtures/linyuan_actual_possible_existence.json').read_text())
    assert actual['error'] is None  # captured before the repair, not a new approval
    assert '可能性' in T.error(actual['title'], actual['proof'], actual['raw_text'])
    item = dict(title=actual['title'], cover_title=actual['cover'],
                subject=actual['proof']['subject'], evidence=actual['proof']['evidence'])
    assert '可能性' in T._candidate_error(item, actual['raw_text'], '林园', [], check_layout=False)
    assert E.error('林园：石油可能有替代产品', actual['cover'], actual['raw_text'])


@pytest.mark.parametrize('subject,obj,modal', [
    ('石油', '替代产品', '可能'), ('市场', '机会', '也许'),
    ('企业', '风险', '或许'), ('行业', '替代', '也可能'),
])
@pytest.mark.parametrize('field', ['title', 'cover'])
def test_each_field_keeps_same_claim_uncertainty(subject, obj, modal, field):
    source = subject + modal + '有' + obj + '。'
    copy = subject + '有' + obj
    assert E.error(copy if field == 'title' else '长期观察',
                   copy if field == 'cover' else '长期观察', source)
    assert E.error(subject + '可能有' + obj, subject + '可能有' + obj, source) is None


@pytest.mark.parametrize('copy,source', [
    ('石油有替代产品', '石油有替代产品。'),
    ('石油可能有替代产品', '石油可能有替代产品。'),
    ('如果石油有替代产品', '石油可能有替代产品。'),
    ('石油是否有替代产品', '石油可能有替代产品。'),
    ('石油没有替代产品', '石油可能有替代产品。'),
    ('煤炭有替代产品', '石油可能有替代产品。'),
    ('市场有机会', '市场可能有风险。'),
    ('企业有风险', '企业可能没有风险。'),
    ('企业有风险', '企业可能有风险投资。'),
    ('企业有风险', '企业可能有风险。企业确实有风险。'),
    ('企业有风险', '企业可能有风险。企业有风险，市场也许有机会。'),
    ('企业有风险', '企业可能有风险。市场可能有机会，但企业确实有风险。'),
    ('企业有风险', '不能说企业有风险。'),
])
def test_normal_counterexamples(copy, source):
    assert E.error(copy, copy, source) is None


def test_unrelated_hedge_does_not_qualify_assertion():
    assert E.error('市场可能有机会，石油有替代产品', '长期观察', '石油也可能有替代产品。')


@pytest.mark.parametrize('extra', ['如果企业有风险。', '不能说企业有风险。', '企业是否有风险？', '如果估计正确，企业有风险。'])
def test_nonaffirmative_repeat_cannot_override_possibility(extra):
    assert E.error('企业有风险', '企业有风险', '企业可能有风险。' + extra)


def test_already_is_not_part_of_the_subject():
    assert E.error('企业已经有风险', '企业有风险', '企业可能有风险。')
