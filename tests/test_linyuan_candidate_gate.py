"""林园自动选片：标题提到他不等于视频里是他本人。"""

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


FC = _load("linyuan_fc_index", "linyuan/fc/index.py")
LOCAL = _load("linyuan_stage_and_dispatch", "linyuan/stage_and_dispatch.py")


BAD_TITLES = [
    "林园也这样看！",  # BV1qMtZ6cEc8 的错误源片：全片是另一位男性
    "与林园并肩的百亿私募巨擘，但斌怎么看科技股",
    "林园遭点名，多家私募年内领到罚单",
    "#远离# #林园# #林园基金#",
    "#价值投资# #林园# #段永平#",
    "2026林园",
]

GOOD_TITLES = [
    "林园：现在传统行业的股票处于几十年低位",
    "林园说医药是值得长期投资的行业",
    "转载第一财经专访林园：科技行业机会和科技股机会是两回事",
    "林园最新演讲：这是我人生最后一次机会",
    "片仔癀股东大会现场，林园发言完整版",
]


def test_only_titles_with_first_person_speaking_evidence_pass():
    for title in BAD_TITLES:
        assert FC.title_has_target_speaker(title) is False, title
        assert LOCAL.title_has_target_speaker(title) is False, title
    for title in GOOD_TITLES:
        assert FC.title_has_target_speaker(title) is True, title
        assert LOCAL.title_has_target_speaker(title) is True, title


def test_bad_sample_never_enters_fc_candidate_pool():
    items = [{
        "id": "weibo_search:5337590116123612",
        "source": "weibo_search",
        "title": "林园也这样看！",
        "url": "https://m.weibo.cn/detail/5337590116123612",
        "video_url": "https://example.invalid/video.mp4",
        "author": "莒人莒味和合发展",
        "extra": {"duration": 134},
    }]
    state = {"dispatched": [], "rejected": [], "published": {}}
    assert FC.pick(items, state, 10) == []

