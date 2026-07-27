"""金句出片门槛（scripts/highlight.py）。达不到就退 2，不硬凑。"""

import json

import pytest

import highlight as HL


def quote(rank, dur):
    return {"rank": rank, "clip_duration_sec": dur,
            "clip_start_sec": rank * 100.0,
            "clip_end_sec": rank * 100.0 + dur}


def test_passing_set_is_returned_trimmed_to_want():
    quotes = [quote(i, 60) for i in range(1, 6)]
    got = HL.enforce_quote_thresholds(quotes, want=3)
    assert [q["rank"] for q in got] == [1, 2, 3]


def test_rejects_when_total_duration_below_threshold(capsys):
    quotes = [quote(i, 29) for i in range(1, 4)]      # 3 × 29 = 87s < 150s
    with pytest.raises(SystemExit) as e:
        HL.enforce_quote_thresholds(quotes, want=3)
    assert e.value.code == HL.EXIT_QUALITY

    payload = json.loads(capsys.readouterr().err.strip())
    assert payload["stage"] == "highlight"
    assert payload["reason"] == "insufficient_duration"
    assert payload["actual_sec"] == 87
    assert payload["threshold_sec"] == 150


def test_rejects_when_too_few_usable_quotes(capsys):
    quotes = [quote(1, 90), quote(2, 90)]
    with pytest.raises(SystemExit) as e:
        HL.enforce_quote_thresholds(quotes, want=3)
    assert e.value.code == HL.EXIT_QUALITY

    payload = json.loads(capsys.readouterr().err.strip())
    assert payload["reason"] == "insufficient_quotes"
    assert payload["actual_count"] == 2
    assert payload["threshold_count"] == 3


def test_short_quotes_are_dropped_before_counting(capsys):
    # 14s 的段落低于 MIN_QUOTE_SEC，不该被算进可用条数
    quotes = [quote(1, 90), quote(2, 90), quote(3, 14)]
    with pytest.raises(SystemExit) as e:
        HL.enforce_quote_thresholds(quotes, want=3)
    assert e.value.code == HL.EXIT_QUALITY
    assert json.loads(capsys.readouterr().err.strip())["reason"] == "insufficient_quotes"


def test_env_can_relax_thresholds(monkeypatch):
    monkeypatch.setenv("HIGHLIGHT_MIN_TOTAL_SEC", "60")
    monkeypatch.setenv("HIGHLIGHT_MIN_QUOTES", "2")
    quotes = [quote(1, 40), quote(2, 40)]
    assert len(HL.enforce_quote_thresholds(quotes, want=2)) == 2


def test_strict_mode_ignores_env_relaxation(monkeypatch):
    # CI 上带 --strict-highlights，免得有人靠环境变量把流水线调绿
    monkeypatch.setenv("HIGHLIGHT_MIN_TOTAL_SEC", "60")
    monkeypatch.setenv("HIGHLIGHT_MIN_QUOTES", "2")
    quotes = [quote(1, 40), quote(2, 40)]
    with pytest.raises(SystemExit) as e:
        HL.enforce_quote_thresholds(quotes, want=2, strict=True)
    assert e.value.code == HL.EXIT_QUALITY


def test_garbage_env_value_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("HIGHLIGHT_MIN_QUOTES", "多来几条")
    assert HL.threshold("HIGHLIGHT_MIN_QUOTES", HL.MIN_QUOTES) == HL.MIN_QUOTES
