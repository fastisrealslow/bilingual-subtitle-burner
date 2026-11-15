import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'linyuan'))
import title_rewrite as T
from title_word_boundary import source_word_boundary_error as check


def test_actual_published_copy_and_cached_proof():
    data = json.loads((Path(__file__).parent / 'fixtures/linyuan_published_split_word.json').read_text())
    proof = data['proof']
    assert '牛市' in check(data['title'], proof['cover'], data['raw_text'])
    assert '词语' in T.error(data['title'], proof, data['raw_text'])
    item = dict(title=data['title'], cover_title=proof['cover'], subject=proof['subject'], evidence=proof['evidence'])
    assert '词语' in T._candidate_error(item, data['raw_text'], '林园', (), check_layout=False)


@pytest.mark.parametrize('source,copy', [
    ('未来三年还是持续的熊市，格局不会改变。', '未来三年还是持续的熊'),
    ('这家公司最重要的是现金流。', '这家公司最重要的是现金'),
    ('他是一个特别厉害的投资者。', '他是一个特别厉害的投资'),
])
def test_variants_check_title_and_cover_independently(source, copy):
    assert check(copy, '保留完整观点', source)
    assert check('保留完整观点', copy, source)


@pytest.mark.parametrize('source,copy', [
    ('未来三年还是持续的牛市，格局不会改变。', '未来三年还是持续的牛市'),
    ('未来三年还是持续的牛市，格局不会改变。', '我相信牛市会持续'),
    ('未来三年还是持续的牛市，格局不会改变。', '这头牛'),
    ('这家公司最重要的是现金流。另一家公司最重要的是现金。', '另一家公司最重要的是现金'),
    ('这家公司最重要的是现金流。以前说这家公司最重要的是现金，现在改了。', '这家公司最重要的是现金'),
    ('我们希望这家公司持续发展。', '我们希望这家公司持续发展'),
    ('这个行业的现金流很稳定。', '我最看重的就是现金'),
])
def test_complete_paraphrased_short_and_independently_supported_copy(source, copy):
    assert check(copy, copy, source) is None


def test_source_is_not_joined_across_punctuation():
    assert check('未来三年还是持续的牛', '', '未来三年还是持续的牛。市值还会变化。') is None
