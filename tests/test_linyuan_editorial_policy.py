"""Prevent the actual short-video regression through all daily entry points."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'linyuan'))
import editorial_policy as policy
import produce_cn as produce


def complete_meta():
    return dict(duration_sec=150, segments=[dict(start=600,end=750)],
        editorial_review=dict(version=policy.VERSION,standalone_opening=True,
            complete_argument=True,reasoning_present=True,natural_ending=True,
            requires_audio_review=False,summary='观点与论据',transcript_sha256='abc'))


def test_real_short_mp4_cannot_pass_with_long_metadata():
    meta=complete_meta()
    assert policy.metadata_error(meta,150) is None
    assert '实际MP4不足120秒' in policy.metadata_error(meta,20)
    assert policy.metadata_error(meta,180) is not None


def test_silence_padding_and_unrelated_splicing_do_not_satisfy_duration():
    meta=complete_meta()
    meta['segments']=[dict(start=600,end=620)]
    assert policy.metadata_error(meta,150) is not None
    meta['segments']=[dict(start=600,end=675),dict(start=900,end=975)]
    assert policy.metadata_error(meta,150) is not None


def test_old_short_highlight_cache_is_not_reused():
    cues=[dict(start=i*30,end=(i+1)*30,text='这是完整论述。') for i in range(6)]
    with tempfile.TemporaryDirectory() as tmp:
        work=Path(tmp)
        (work/'highlights.json').write_text('[{"start":0,"end":0,"score":9}]')
        with patch.object(produce,'llm',return_value='[{"start":0,"end":4,"score":8,"reason":"完整论述"}]') as ask:
            picks=produce.pick_highlights(cues,'林园','test',work)
        assert ask.call_count==1
        assert policy.range_seconds(cues,picks[0])==150


def test_preselected_shortcut_rejected_before_copy_or_render():
    with tempfile.TemporaryDirectory() as tmp:
        with patch.object(produce,'copywrite') as copywrite:
            try:
                produce._produce_one(Path('source.mp4'),Path(tmp),Path(tmp),
                    [dict(start=0,end=20,text='完整一句，但过短。')],
                    '林园','采访','key',False,1280,720,'_1',
                    preselected_picks=[dict(start=0,end=0,score=9,reason='短句')])
            except ValueError as exc:
                assert '不足120秒' in str(exc)
            else:
                raise AssertionError('Preselected short clip escaped duration gate')
            copywrite.assert_not_called()


def test_unresolved_negation_or_number_requires_audio_review():
    meta=complete_meta()
    meta['editorial_review']['requires_audio_review']=True
    assert policy.metadata_error(meta) is not None


def test_daily_and_explicit_publication_share_the_same_gate():
    spec=importlib.util.spec_from_file_location('editorial_fc',ROOT/'linyuan/fc/index.py')
    fc=importlib.util.module_from_spec(spec);spec.loader.exec_module(fc)
    meta=complete_meta();meta['duration_sec']=20;meta['quality_gate_version']=fc.QUALITY_GATE_VERSION
    assert '不足120秒' in fc.artifact_quality_error(meta)
    assert fc.fresh_six_budget({'date':'2026-09-07','count':0},'ly-fresh-six-0906-05','2026-09-07') is None
