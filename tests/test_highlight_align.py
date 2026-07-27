"""切点对齐句子边界（scripts/highlight.py）。"""

import highlight as H


def entries(*specs):
    """specs: (start, end, text) 三元组。"""
    return [{"index": i + 1, "start_sec": s, "end_sec": e, "text": t}
            for i, (s, e, t) in enumerate(specs)]


ENTRIES = entries(
    (0.0, 3.0, "This is the first sentence."),
    (3.0, 6.0, "It keeps going and going"),
    (6.0, 9.0, "and finally stops here."),
    (9.0, 12.0, "A new thought begins"),
    (12.0, 15.0, "which ends right about now."),
    (15.0, 18.0, "Tail without any terminator"),
)


def item(start, end, **kw):
    return {"start_sec": start, "end_sec": end, **kw}


# ── snap_start / snap_end ────────────────────────────────────────────────────

def test_snap_start_moves_to_char_after_punctuation():
    # 起点落在 "It keeps going" 中间，应回退到上一句句号之后那条字幕的开头
    assert H.snap_start(4.5, ENTRIES) == 3.0


def test_snap_end_extends_to_sentence_terminator():
    # 终点落在 "and finally stops here." 中间，应延到该条字幕结束
    assert H.snap_end(7.0, ENTRIES) == 9.0


def test_snap_start_no_punctuation_in_window_keeps_original():
    small = entries((0.0, 3.0, "no terminator here"),
                    (3.0, 6.0, "still nothing"))
    assert H.snap_start(5.0, small) == 5.0


def test_snap_end_no_punctuation_in_window_keeps_original():
    small = entries((0.0, 3.0, "no terminator here"),
                    (3.0, 6.0, "still nothing"))
    assert H.snap_end(1.0, small) == 1.0


def test_window_limit_blocks_far_away_punctuation():
    far = entries((0.0, 3.0, "Ends with a period."),
                  (3.0, 60.0, "an extremely long unpunctuated stretch"))
    # 句号在 3s，起点在 55s，超出 ±8s 窗口，不得外扩
    assert H.snap_start(55.0, far, window_sec=8.0) == 55.0
    assert H.snap_start(55.0, far, window_sec=60.0) == 3.0


def test_cjk_terminators_recognised():
    zh = entries((0.0, 3.0, "他这么说的。"), (3.0, 6.0, "接着又讲了别的"))
    assert H.snap_start(4.0, zh) == 3.0


def test_terminator_behind_closing_quote_still_counts():
    q = entries((0.0, 3.0, '他说「就是这样。」'), (3.0, 6.0, "然后停了一下"))
    assert H.ends_sentence('他说「就是这样。」')
    assert H.snap_start(4.0, q) == 3.0


# ── align_clips ─────────────────────────────────────────────────────────────

def test_align_adds_margin_after_snapping():
    out = H.align_clips([item(4.5, 7.0)], ENTRIES, total_duration=100.0,
                        min_sec=0, max_sec=1000)[0]
    assert out["clip_start_sec"] == 3.0 - 0.3
    assert out["clip_end_sec"] == 9.0 + 0.3


def test_align_without_punctuation_only_adds_margin():
    small = entries((0.0, 30.0, "one long unpunctuated stretch of speech"))
    out = H.align_clips([item(10.0, 20.0)], small, total_duration=100.0,
                        min_sec=0, max_sec=1000)[0]
    assert out["clip_start_sec"] == 9.7
    assert out["clip_end_sec"] == 20.3


def test_clamped_to_video_bounds():
    out = H.align_clips([item(0.0, 18.0)], ENTRIES, total_duration=18.0,
                        min_sec=0, max_sec=1000)[0]
    assert out["clip_start_sec"] == 0.0          # 不会变成 -0.3
    assert out["clip_end_sec"] == 18.0           # 不会超过总时长


def test_zero_total_duration_means_unknown_not_empty_clip():
    # --total-duration 缺省时不应把所有片段压成 0 长度
    out = H.align_clips([item(4.5, 7.0)], ENTRIES, total_duration=0.0,
                        min_sec=0, max_sec=1000)[0]
    assert out["clip_end_sec"] == 9.3


def test_adjacent_clips_split_at_midpoint_when_overlapping():
    long_entries = entries(
        (0.0, 10.0, "First chunk ends."),
        (10.0, 20.0, "second chunk with no stop"),
        (20.0, 30.0, "third chunk ends."),
    )
    # 两段各自扩展后会咬到一起，必须在中点切开
    out = H.align_clips([item(5.0, 12.0), item(13.0, 25.0)], long_entries,
                        total_duration=30.0, min_sec=0, max_sec=1000)
    first, second = out
    assert first["clip_end_sec"] <= second["clip_start_sec"]
    assert first["clip_end_sec"] == second["clip_start_sec"]


def test_non_overlapping_clips_are_left_alone():
    out = H.align_clips([item(1.0, 2.0), item(15.5, 16.0)], ENTRIES,
                        total_duration=100.0, min_sec=0, max_sec=1000)
    assert out[0]["clip_end_sec"] < out[1]["clip_start_sec"]


def test_overlap_resolution_handles_unsorted_input():
    # highlights 是按分数排序的，时间上未必有序
    long_entries = entries((0.0, 30.0, "no stops at all here"))
    out = H.align_clips([item(20.0, 25.0), item(5.0, 21.0)], long_entries,
                        total_duration=30.0, min_sec=0, max_sec=1000)
    later, earlier = out
    assert earlier["clip_end_sec"] == later["clip_start_sec"]


def test_min_and_max_duration_still_enforced():
    long_entries = entries((0.0, 400.0, "one very long unpunctuated take"))
    short = H.align_clips([item(10.0, 12.0)], long_entries, total_duration=400.0,
                          min_sec=20, max_sec=180)[0]
    assert short["clip_duration_sec"] == 20

    huge = H.align_clips([item(10.0, 350.0)], long_entries, total_duration=400.0,
                         min_sec=20, max_sec=180)[0]
    assert huge["clip_duration_sec"] == 180


def test_hms_fields_track_seconds():
    out = H.align_clips([item(4.5, 7.0)], ENTRIES, total_duration=100.0,
                        min_sec=0, max_sec=1000)[0]
    assert out["clip_start"] == H.sec2hms(out["clip_start_sec"])
    assert out["clip_end"] == H.sec2hms(out["clip_end_sec"])
    assert out["clip_duration_sec"] == out["clip_end_sec"] - out["clip_start_sec"]


def test_original_item_fields_preserved():
    out = H.align_clips([item(4.5, 7.0, rank=1, score=9.0)], ENTRIES,
                        total_duration=100.0)[0]
    assert out["rank"] == 1 and out["score"] == 9.0
    assert out["start_sec"] == 4.5   # 原始转写边界不被改写


def test_empty_entries_falls_back_to_margin_only():
    out = H.align_clips([item(10.0, 40.0)], [], total_duration=100.0,
                        min_sec=0, max_sec=1000)[0]
    assert (out["clip_start_sec"], out["clip_end_sec"]) == (9.7, 40.3)
