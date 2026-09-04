"""跨渠道种子归一化和去重。"""

import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location(
    "linyuan_seed_manager", ROOT / "linyuan/seed_manager.py")
SEEDS = importlib.util.module_from_spec(spec)
spec.loader.exec_module(SEEDS)


def test_normalize_all_supported_seed_formats():
    assert SEEDS.normalize_douyin("https://www.douyin.com/video/7526178531999583551") == \
        "https://www.douyin.com/video/7526178531999583551"
    assert SEEDS.normalize_haokan("https://haokan.baidu.com/v?vid=1508795756322186890") == \
        "1508795756322186890"
    assert SEEDS.normalize_netease("https://www.163.com/v/video/VFABC1234.html") == \
        "VFABC1234"
    assert SEEDS.normalize_netease("vfabc1234") == "VFABC1234"
    assert SEEDS.normalize_yicai("https://www.yicai.com/video/103329354.html") == \
        "103329354"


def test_netease_add_is_idempotent(monkeypatch, tmp_path):
    path = tmp_path / "netease_seeds.json"
    path.write_text(json.dumps({"vcodes": ["VA141983Q"]}), encoding="utf-8")
    monkeypatch.setattr(SEEDS, "NETEASE_SEEDS", path)

    result = SEEDS.add_seeds(
        "netease", ["va141983q", "https://www.163.com/v/video/VFABC1234.html", "bad!"])

    assert result["added"] == ["VFABC1234"]
    assert result["skipped"] == 1
    assert result["invalid"] == 1
    assert json.loads(path.read_text())["vcodes"] == ["VA141983Q", "VFABC1234"]
