"""外层单书名号 ``『』`` 归一化到 ``「」``（scripts/platform_rules.py）。

分层规范：外层「」，内层『』。PR #1 只把 ASCII / 弯引号转成了直角引号，
没有处理最外层已经是『』的情况，DeepSeek-V3 译文里的「他只说『不行』」
因此能一路穿透到烧字幕那一步。
"""

import platform_rules as R

n = R.normalize_cjk_punctuation


def test_outer_white_brackets_promoted_to_corner():
    assert n("他只说『不行』") == "他只说「不行」"


def test_nested_white_brackets_kept_inside_corner():
    # 嵌套在「」里的『』是正确的内层形式，不能动
    assert n("他说「查理只说『不』」") == "他说「查理只说『不』」"


def test_only_paired_white_brackets_promoted():
    # 落单的『配不上对，原样留着；成对的那组照常提升
    assert n("『开头引号』但结尾没配对『") == "「开头引号」但结尾没配对『"


def test_deepseek_v3_translation_sample():
    # DeepSeek-V3 实际译文样本
    assert (n("我给芒格打电话 他只说『不行』")
            == "我给芒格打电话 他只说「不行」")


def test_multiple_outer_pairs_all_promoted():
    assert n("他说『不行』我说『为什么』") == "他说「不行」我说「为什么」"


def test_single_curly_quotes_land_on_outer_corner():
    # ‘’ 一律映射成『』，在最外层时同样要提升 —— 这条以前产出的是『安全边际』
    assert n("所谓‘安全边际’") == "所谓「安全边际」"


def test_outer_white_brackets_alongside_existing_corner():
    assert n("「安全边际」和『复利』") == "「安全边际」和「复利」"


def test_mismatched_brackets_left_alone():
    # 括号类型对不上说明文案本身是坏的，不要自作主张改写
    assert n("「a『b」c』") == "「a『b」c』"


def test_text_without_white_brackets_untouched():
    assert n("他说「不行」") == "他说「不行」"
