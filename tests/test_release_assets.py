"""Release 资产摊平与正文渲染（scripts/release_assets.py）。

单集的产物在 ``deliver/<slug>/``、多集在 ``ep01/`` 子目录，Release 上一律是
扁平的 ``ep01.mp4``。搬错了手机页上的下载按钮就是 404。
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import release_assets as RA                       # noqa: E402


def files_for(eid):
    return {"video": f"{eid}.mp4",
            "cover_16x9": f"{eid}_cover_16x9.jpg",
            "cover_9x16": f"{eid}_cover_9x16.jpg"}


def episode(eid, title="标题", duration=198.92):
    return {"index": int(eid[2:]), "id": eid, "title": title,
            "duration_sec": duration, "scheduled_date": "2026-07-28",
            "files": files_for(eid),
            "urls": {k: f"https://example.com/{v}"
                     for k, v in files_for(eid).items()}}


def make_delivery(tmp_path, ids, per_episode_dirs):
    """造出 deliver/<slug>/ 的目录树，返回 queue.json 路径。"""
    slug_dir = tmp_path / "deliver" / "munger"
    for eid in ids:
        d = slug_dir / eid if per_episode_dirs else slug_dir
        d.mkdir(parents=True, exist_ok=True)
        (d / "final.mp4").write_bytes(f"video-{eid}".encode())
        (d / "cover_16x9.jpg").write_bytes(b"wide")
        (d / "cover_9x16.jpg").write_bytes(b"tall")

    queue = {"schema": 1, "slug": "munger", "speaker": "查理·芒格",
             "generated_at": "2026-07-27T14:47:00Z", "commit": "deadbeef",
             "release_tag": "clips-munger",
             "episodes": [episode(eid) for eid in ids]}
    path = slug_dir / "queue.json"
    path.write_text(json.dumps(queue, ensure_ascii=False), encoding="utf-8")
    return path


def test_multi_episode_dirs_are_flattened(tmp_path):
    q = make_delivery(tmp_path, ["ep01", "ep02"], per_episode_dirs=True)
    out = tmp_path / "_release"

    assert RA.main(["--queue", str(q), "--out", str(out)]) == 0

    assert sorted(p.name for p in out.iterdir()) == [
        "ep01.mp4", "ep01_cover_16x9.jpg", "ep01_cover_9x16.jpg",
        "ep02.mp4", "ep02_cover_16x9.jpg", "ep02_cover_9x16.jpg",
        "queue.json"]
    assert (out / "ep02.mp4").read_bytes() == b"video-ep02"


def test_single_episode_at_the_slug_root_is_found(tmp_path):
    """单集不建 ep01/ 子目录，仍要能摊成 ep01.mp4。"""
    q = make_delivery(tmp_path, ["ep01"], per_episode_dirs=False)
    out = tmp_path / "_release"

    assert RA.main(["--queue", str(q), "--out", str(out)]) == 0
    assert (out / "ep01.mp4").read_bytes() == b"video-ep01"
    assert (out / "ep01_cover_16x9.jpg").is_file()


def test_queue_json_is_itself_an_asset(tmp_path):
    q = make_delivery(tmp_path, ["ep01"], per_episode_dirs=True)
    out = tmp_path / "_release"
    RA.main(["--queue", str(q), "--out", str(out)])
    assert json.loads((out / "queue.json").read_text(encoding="utf-8"))["slug"] \
        == "munger"


def test_rerunning_overwrites_instead_of_duplicating(tmp_path):
    q = make_delivery(tmp_path, ["ep01"], per_episode_dirs=True)
    out = tmp_path / "_release"
    RA.main(["--queue", str(q), "--out", str(out)])
    (tmp_path / "deliver" / "munger" / "ep01" / "final.mp4").write_bytes(b"new")
    RA.main(["--queue", str(q), "--out", str(out)])

    assert (out / "ep01.mp4").read_bytes() == b"new"
    assert len(list(out.glob("*.mp4"))) == 1


def test_missing_product_exits_one(tmp_path, capsys):
    q = make_delivery(tmp_path, ["ep01"], per_episode_dirs=True)
    (tmp_path / "deliver" / "munger" / "ep01" / "final.mp4").unlink()

    assert RA.main(["--queue", str(q), "--out", str(tmp_path / "_r")]) == 1
    assert "缺少产物" in capsys.readouterr().err


def test_files_missing_a_key_exits_one(tmp_path, capsys):
    """queue 里少一个 files 键就退 1 —— 漏传的封面在手机页上就是个 404 按钮。"""
    q = make_delivery(tmp_path, ["ep01"], per_episode_dirs=True)
    queue = json.loads(q.read_text(encoding="utf-8"))
    del queue["episodes"][0]["files"]["cover_9x16"]
    q.write_text(json.dumps(queue, ensure_ascii=False), encoding="utf-8")

    assert RA.main(["--queue", str(q), "--out", str(tmp_path / "_r")]) == 1
    assert "cover_9x16" in capsys.readouterr().err


def test_missing_queue_exits_one(tmp_path, capsys):
    assert RA.main(["--queue", str(tmp_path / "nope.json"),
                    "--out", str(tmp_path / "_r")]) == 1
    assert "找不到" in capsys.readouterr().err


# ── Release 正文 ────────────────────────────────────────────────────────────

def test_notes_list_every_episode_with_title_duration_and_link(tmp_path):
    q = make_delivery(tmp_path, ["ep01", "ep02"], per_episode_dirs=True)
    notes = tmp_path / "notes.md"
    RA.main(["--queue", str(q), "--out", str(tmp_path / "_r"),
             "--notes", str(notes)])

    body = notes.read_text(encoding="utf-8")
    assert "ep01" in body and "ep02" in body
    assert "3 分 19 秒" in body                 # 198.92s
    assert "https://example.com/ep02.mp4" in body


def test_notes_are_not_written_without_the_flag(tmp_path):
    q = make_delivery(tmp_path, ["ep01"], per_episode_dirs=True)
    out = tmp_path / "_release"
    RA.main(["--queue", str(q), "--out", str(out)])
    assert not list(out.glob("*.md"))
