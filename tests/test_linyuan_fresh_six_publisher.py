"""The fresh-six verifier must validate public archives without calling FC."""
import importlib.util
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "linyuan"))
sys.path.insert(0, str(ROOT / "linyuan" / "fc"))
spec = importlib.util.spec_from_file_location(
    "publish_fresh_six", ROOT / "linyuan" / "fc" / "publish_fresh_six.py")
publisher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(publisher)


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def read(self):
        return json.dumps(self.payload).encode()


class FakeOpener:
    def __init__(self, payloads):
        self.payloads = iter(payloads)

    def open(self, request, timeout=0):
        return FakeResponse(next(self.payloads))


def test_runner_publication_status_requires_exact_public_owner(monkeypatch):
    good = {"code": 0, "data": {"state": 0, "duration": 30,
            "owner": {"mid": int(publisher.fc.OWNER_MID)}, "bvid": "BVgood"}}
    wrong_owner = {"code": 0, "data": {"state": 0, "duration": 30,
                   "owner": {"mid": 1}, "bvid": "BVwrong"}}
    monkeypatch.setattr(
        publisher, "bilibili_opener", lambda: FakeOpener([good, wrong_owner]))
    result = publisher.runner_publication_status({
        "sha-good": {"bvid": "BVgood", "title": "good"},
        "sha-wrong": {"bvid": "BVwrong", "title": "wrong"},
    })
    assert result["verification_origin"] == "github-actions"
    assert result["receipts"] == 2
    assert result["public_count"] == 1
    assert result["videos"][0]["public"] is True
    assert result["videos"][1]["public"] is False
