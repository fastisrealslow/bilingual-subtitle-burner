"""Regressions for accepted/bad/accepted batches and daily release selection."""
import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'linyuan'))
import produce_cn as produce
from batch_delivery import archive_accepted

spec = importlib.util.spec_from_file_location('batch_test_fc', ROOT / 'linyuan/fc/index.py')
fc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fc)


class BatchIsolationTests(unittest.TestCase):
    def test_bad_middle_part_does_not_erase_success_or_skip_next_part(self, split=False):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            source = base / 'source.mp4'
            source.touch()
            cues = [dict(start=i*30, end=(i+1)*30, text='完整句子') for i in range(3)]
            report = dict(resolution=dict(width=1280,height=720),
                          visual_identity={},clean_filter_verified=True)
            calls = []

            def render(src, work, out, cues, speaker, occasion, key, existing, w, h, suffix, **kwargs):
                calls.append(suffix)
                (out / f'final{suffix}.mp4').write_bytes(b'encoded-part')
                if suffix == '_2':
                    raise produce.VisualQualityError('真人动态区检出持续黑边/错误取景')
                result = dict(final=f'final{suffix}.mp4',title='完整标题'+suffix,
                              duration_sec=90, resolution=dict(width=720,height=1280),
                              render_mode='live_video_card',watermark_verified=True)
                for k, name in [('cover',f'cover{suffix}.jpg'),('preview_30s',f'preview_30s{suffix}.mp4'),
                                ('contact_sheet_6',f'contact_sheet_6{suffix}.jpg')]:
                    (out/name).write_bytes(b'accepted')
                    result[k] = name
                return result

            with patch.object(produce,'BASE',base), patch.object(produce,'load_key',return_value='test'), \
                 patch.object(produce,'run_source_quality_gate',return_value={**report,'passed':True}), \
                 patch.object(produce,'transcribe',return_value=cues), \
                 patch.object(produce,'_chunk_by_time',return_value=[(0,0),(1,1),(2,2)]), \
                 patch.object(produce,'_dedup_chunks_char',side_effect=lambda chunks,*a:chunks), \
                 patch.object(produce,'_dedup_chunks_by_llm',side_effect=lambda chunks,*a:chunks), \
                 patch.object(produce,'pick_highlights',return_value=[dict(start=0,end=0,score=8)]), \
                 patch.object(produce,'_produce_one',side_effect=render), \
                 patch.object(sys,'argv',['produce','--source',str(source),'--slug','case']+(['--split-highlights'] if split else [])):
                self.assertEqual(produce.main(),0)
            out=base/'deliver/case'
            rows=json.loads((out/'meta.json').read_text())
            self.assertEqual(calls,['_1','_2','_3'])
            self.assertEqual([m['final'] for m in rows],['final_1.mp4','final_3.mp4'])
            self.assertFalse((out/'final_2.mp4').exists())
            self.assertTrue((out/'_tmp/rejected/2/final_2.mp4').exists())
            self.assertEqual(json.loads((out/'batch_report.json').read_text())['rejected'][0]['part'],2)
            # Even a stray root-level MP4 is not release-packaged.
            (out/'final_bad.mp4').write_bytes(b'bad')
            self.assertEqual(archive_accepted(out,'case'),2)
            self.assertFalse((out/'_accepted/case.final_bad.mp4').exists())
            self.assertTrue((out/'_accepted/final_3.mp4').exists())
            self.assertFalse((out/'_accepted/final_2.mp4').exists())

    def test_independent_highlights_keep_other_successes_after_bad_middle(self):
        self.test_bad_middle_part_does_not_erase_success_or_skip_next_part(split=True)

    def test_manual_batch_without_candidate_keys_does_not_crash_daily_picker(self):
        state=dict(dispatched=[dict(slug='manual',ts=time.time())],
                   rejected=[dict(slug='old')],published={})
        self.assertEqual(fc.pick([],state,6),[])

    def test_stale_unfinished_jobs_do_not_fill_inventory(self):
        state=dict(dispatched=[dict(slug='stale',ts=1),dict(slug='running',ts=time.time(),production_rules_version=fc.PRODUCTION_RULES_VERSION)],published={})
        self.assertEqual(fc._pending_final_count(state),1)

    def test_legacy_batch_override_cannot_exceed_six_daily_releases(self):
        today=time.strftime('%Y-%m-%d',time.gmtime(time.time()+8*3600))
        state=dict(dispatched=[dict(slug='manual',ts=time.time())],published={},
                   daily_publish=dict(date=today,count=6))
        with patch.object(fc,'load_state',return_value=state), \
             patch.object(fc,'save_state'), patch.object(fc,'_collect_source_rejections',return_value=0), \
             patch.object(fc,'gh',side_effect=AssertionError('must stop before upload/artifact network')):
            self.assertEqual(fc.publish_handler(dict(batch_slug='manual',ignore_daily_limit=True,force_publish=True)),{'published':0})

    def test_obsolete_partial_batch_does_not_block_new_production(self):
        state=dict(dispatched=[dict(slug='old',ts=time.time(),published_parts=1)],
                   published={'old':dict(parts_total=53)})
        self.assertEqual(fc._pending_final_count(state),0)

    def test_audio_card_requires_verified_live_supply_and_at_most_one_per_six(self):
        card=dict(render_mode='audio_card')
        self.assertIsNotNone(fc.daily_mix_error(card,{}))
        self.assertIsNotNone(fc.daily_mix_error(card,dict(count=4)))
        self.assertIsNone(fc.daily_mix_error(card,dict(live_video_count=3)))
        self.assertIsNotNone(fc.daily_mix_error(card,dict(live_video_count=4,audio_card_count=1)))
        self.assertIsNone(fc.daily_mix_error(dict(render_mode='live_video_card'),{}))

    def test_fresh_six_is_dated_scoped_and_preserves_real_old_totals(self):
        daily = dict(date='2026-09-06', count=10, live_video_count=10)
        self.assertIsNone(fc.fresh_six_budget(daily, 'old-batch', '2026-09-06'))
        self.assertIsNone(fc.fresh_six_budget(daily, 'ly-fresh-six-0906-01', '2026-09-07'))
        fresh = fc.fresh_six_budget(daily, 'ly-fresh-six-0906-01', '2026-09-06')
        self.assertEqual(daily['count'], 10)
        self.assertEqual(fresh['count'], 0)
        self.assertIsNotNone(fc.daily_mix_error(dict(render_mode='audio_card'), fresh))
        fresh['count'] = 6
        self.assertIs(fc.fresh_six_budget(daily, 'ly-fresh-six-0906-02', '2026-09-06'), fresh)
        self.assertEqual(daily['count'], 10)

    def test_fresh_six_stops_at_six_even_when_forced(self):
        now = 1788681600  # 2026-09-06 16:00 Beijing
        state = dict(dispatched=[dict(slug='ly-fresh-six-0906-01', ts=now)], published={},
                     daily_publish=dict(date='2026-09-06', count=16,
                                        fresh_six=dict(date='2026-09-06', count=6)))
        with patch.object(fc.time, 'time', return_value=now), \
             patch.object(fc, 'load_state', return_value=state), \
             patch.object(fc, 'save_state'), patch.object(fc, '_collect_source_rejections', return_value=0), \
             patch.object(fc, 'gh', side_effect=AssertionError('quota must stop before network')):
            self.assertEqual(fc.publish_handler(dict(batch_slug='ly-fresh-six-0906-01',
                force_publish=True, ignore_daily_limit=True)), {'published': 0})

    def test_fresh_six_requires_exact_reviewed_video_and_source(self):
        import hashlib
        with tempfile.TemporaryDirectory() as tmp:
            video = Path(tmp)/'final.mp4'; video.write_bytes(b'reviewed-video')
            sha = hashlib.sha256(video.read_bytes()).hexdigest()
            meta = dict(fingerprints=dict(sha256=sha))
            slug, source = 'ly-fresh-six-0906-01', 'https://example.com/original'
            self.assertIsNotNone(fc.fresh_six_review_error(meta, video, slug, source))
            with patch.object(fc, 'FRESH_SIX_APPROVED', {sha: dict(slug=slug, source_url=source)}):
                self.assertIsNone(fc.fresh_six_review_error(meta, video, slug, source))
                self.assertIsNotNone(fc.fresh_six_review_error(meta, video, slug, source+'-other'))
                video.write_bytes(b'unreviewed-replacement')
                self.assertIsNotNone(fc.fresh_six_review_error(meta, video, slug, source))


if __name__ == '__main__':
    unittest.main()
