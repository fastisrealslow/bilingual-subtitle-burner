"""Honorifics do not turn an explicit speech clue into an unknown speaker.

Metadata admission remains a clue, never final identity or media approval.
"""
from pathlib import Path
import sys

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan/fc'))
import index as F


@pytest.mark.parametrize('title',[
    '林园总聊最新投资机会20261016',
    '林园总聊投资医药逻辑20201030',
    '林园老师表示，仍需研究公司',
    '林园先生：不要着急下结论',
    '林园总谈银行业？',
    '林园聊投资',
    '林园讲述自己当年的选择',
])
def test_actual_honorific_speech_forms_are_only_metadata_clues(title):
    assert F.title_has_target_speaker(title)
    assert F.item_has_target_speaker(dict(title=title),{})


@pytest.mark.parametrize('title',[
    '林园也这样看！',
    '林园朋友谈投资',
    '林园基金这次亏了多少钱',
    '林园私募的投资体系深度分析',
    '我如何看林园老师',
    '林园总公司如何做投资',
    '林园老师的成功秘诀',
    '林园总260714',
    '林园投资者教育课堂-价值投资的底层逻辑 张海辉',
    '林园老师怎么聊这个话题',
])
def test_mentions_commentary_and_company_or_teacher_names_stay_unknown(title):
    assert not F.title_has_target_speaker(title)


def test_ai_and_reuse_bans_remain_separate_unchanged_admission_gates():
    assert F.AI_NOISE.search('AI模拟林园老师聊投资')
    assert F.title_has_target_speaker('林园：我认为还应研究')
    from source_admission_guard import restriction
    url='https://www.bilibili.com/video/BV1esJv6rEgQ'
    assert restriction(url,[dict(bvid='BV1esJv6rEgQ',no_reprint=1)],policies=[])
    assert restriction(url,[dict(bvid='BV1esJv6rEgQ',direct_dispatch=False)],policies=[])


@pytest.mark.parametrize('title',[
    'AI模拟林园老师聊投资','ai拟声：林园总说投资',
    '人工智能生成的林园总聊市场','林园总聊投资（AI合成）',
])
def test_explicit_synthetic_speaker_labels_do_not_get_an_honorific_exception(title):
    assert F.AI_NOISE.search(title)


def test_real_guest_discussing_ai_is_not_a_synthetic_speaker_label():
    title='林园总聊AI大时代对医药行业的影响'
    assert F.title_has_target_speaker(title) and not F.AI_NOISE.search(title)
