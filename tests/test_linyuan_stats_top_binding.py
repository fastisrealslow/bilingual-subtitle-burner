from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan'))
from collect_stats import top_video_fields


def test_latest_title_is_not_paired_with_a_different_historical_maximum():
    rows=[dict(title='最新视频',play=46),dict(title='真正最高播放的视频',play=1249)]
    assert top_video_fields(rows)==dict(top_title='真正最高播放的视频',top_play=1249)
    assert top_video_fields(rows[::-1])==top_video_fields(rows)
    assert top_video_fields([])==dict(top_title='',top_play=0)
