"""Regression checks for broad, deduplicated Bilibili source discovery."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "linyuan"))

import monitor_v2


def test_bilibili_search_combines_configured_keywords_without_duplicate_bvid(monkeypatch):
    source = monitor_v2.BilibiliSearchSource({
        "keyword": "林园",
        "keywords": ["林园", "林园 完整访谈", "林园"],
    }, {})
    calls = []

    def fake_fetch(keyword):
        calls.append(keyword)
        common = {"bvid": "BVcommon", "title": "林园完整访谈",
                  "view_count": 1, "up": "媒体"}
        if keyword == "林园":
            return [common]
        return [common, {"bvid": "BVfull", "title": "林园演讲完整版",
                         "view_count": 2, "up": "媒体"}]

    monkeypatch.setattr(source, "_fetch_via_api", fake_fetch)
    items = source.fetch(None)

    assert calls == ["林园", "林园 完整访谈"]
    assert [item["id"] for item in items] == [
        "bilibili_search:BVcommon", "bilibili_search:BVfull"]
