"""林园流水线：480P 底线、v3 视觉标准、内容指纹和主题冷却。"""

import importlib.util
import io
import json
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "linyuan"))
import produce_cn as P  # noqa: E402


def test_local_text_backend_never_calls_cloud(monkeypatch,tmp_path):
    monkeypatch.setattr(P,'TEXT_BACKEND','local')
    monkeypatch.setattr(P,'LOCAL_LLM_URL','http://127.0.0.1:11434/api/chat')
    monkeypatch.setattr(P,'BASE',tmp_path)
    seen={}
    class Reply:
        def __enter__(self):return self
        def __exit__(self,*args):return False
        def read(self):return b'{"message":{"content":"local result"}}'
    def open_local(request,timeout):
        seen['url']=request.full_url
        seen['payload']=json.loads(request.data)
        assert 'Authorization' not in request.headers
        return Reply()
    monkeypatch.setattr(P.urllib.request,'urlopen',open_local)
    assert P.llm([{'role':'user','content':'x'}],'paid-key')=='local result'
    assert seen['url'].startswith('http://127.0.0.1:')
    assert seen['payload']['think'] is False
    assert seen['payload']['format']=='json'
    assert seen['payload']['keep_alive']=='24h'
    assert seen['payload']['options']['num_predict']==2000
    assert seen['payload']['options']['num_ctx']==16384


def test_local_timeout_remains_retryable_and_never_caches_no_highlights(monkeypatch,tmp_path):
    monkeypatch.setattr(P,'TEXT_BACKEND','local')
    monkeypatch.setattr(P,'BASE',tmp_path)
    calls=[]
    def timeout(request,timeout):
        calls.append(timeout)
        raise TimeoutError('slow prompt evaluation')
    monkeypatch.setattr(P.urllib.request,'urlopen',timeout)
    cues=[dict(start=i*10,end=(i+1)*10,text='原始完整字幕') for i in range(20)]
    with pytest.raises(P.LocalTextUnavailable,match='保留ASR'):
        P.pick_highlights(cues,'林园','',tmp_path)
    assert len(calls)==1 and 590<calls[0]<=600
    assert not (tmp_path/'highlights.json').exists()
    assert not list((tmp_path/'.llm_cache').glob('*.json'))
    assert issubclass(P.LocalTextUnavailable,P.EditorialReviewUnavailable)


def test_local_truncated_response_cannot_be_cached_as_complete(monkeypatch,tmp_path):
    monkeypatch.setattr(P,'TEXT_BACKEND','local')
    monkeypatch.setattr(P,'BASE',tmp_path)
    class Reply:
        def __enter__(self):return self
        def __exit__(self,*args):return False
        def read(self):return b'{"message":{"content":"[]"},"done_reason":"length"}'
    monkeypatch.setattr(P.urllib.request,'urlopen',lambda *a,**kw:Reply())
    with pytest.raises(P.LocalTextUnavailable,match='输出达到长度上限'):
        P.llm([{'role':'user','content':'review'}],'')
    assert not list((tmp_path/'.llm_cache').glob('*.json'))


def test_editorial_schema_evidence_only_contains_retained_source(monkeypatch,tmp_path):
    cues=[dict(start=0,end=120,text='医药需求随老龄化增长。这是我们的判断。'),
          dict(start=120,end=240,text='这句不在选段中')]
    def review(messages,api_key,**options):
        assert options['max_tokens']==2200
        evidence=options['response_schema']['properties']['analysis']['properties']['audio_issues']['items']['properties']['quote']['enum']
        assert '医药需求随老龄化增长' in evidence
        assert all(quote in cues[0]['text'] for quote in evidence)
        assert '这句不在选段中' not in evidence
        return json.dumps(dict(standalone_opening=True,complete_argument=True,
            reasoning_present=True,natural_ending=True,requires_audio_review=False,
            summary='解释老龄化与医药需求',issues=[],issue_details=[],
            opening_quote='医药需求随老龄化增长。',ending_quote='这是我们的判断。'))
    monkeypatch.setattr(P,'llm',review)
    result=P.review_complete_argument(cues,[dict(start=0,end=0)],'林园','',tmp_path,'')
    assert result['review_prompt_version']==6
    assert (tmp_path/'editorial_response-0.txt').exists()


def test_review_evidence_rejoins_display_rows_before_checking_completeness(monkeypatch,tmp_path):
    cues=[dict(start=0,end=60,text='科技股它现在还在走牛市，传统的股票它'),
          dict(start=60,end=120,text='是在走熊市。')]
    def review(messages,api_key,**options):
        evidence=options['response_schema']['properties']['analysis']['properties']['audio_issues']['items']['properties']['quote']['enum']
        assert cues[0]['text'] not in evidence
        assert ''.join(c['text'] for c in cues) in evidence
        return json.dumps(dict(standalone_opening=True,complete_argument=True,
            reasoning_present=True,natural_ending=True,requires_audio_review=False,
            summary='测试完整句证据传输',issues=[],issue_details=[],
            opening_quote=cues[0]['text'],ending_quote=cues[1]['text']))
    monkeypatch.setattr(P,'llm',review)
    P.review_complete_argument(cues,[dict(start=0,end=1)],'林园','',tmp_path,'')


def test_model_cannot_substitute_middle_quote_for_actual_unanswered_ending(monkeypatch,tmp_path):
    cues=[dict(start=0,end=120,text='我们投资慢性病治疗。这能延长生命。那其他行业呢？')]
    response=dict(standalone_opening=True,complete_argument=True,reasoning_present=True,
        natural_ending=True,requires_audio_review=False,summary='慢性病投资',issues=[],
        opening_quote='我们投资慢性病治疗。',ending_quote='这能延长生命。')
    monkeypatch.setattr(P,'llm',lambda *a,**kw:json.dumps(response))
    with pytest.raises(P.EditorialReviewUnavailable,match='不是实际选段的结尾'):
        P.review_complete_argument(cues,[dict(start=0,end=0)],'林园','',tmp_path,'')
    assert not (tmp_path/'editorial_review.json').exists()


def test_sentence_display_preserves_split_words_and_original_indices():
    cues=[dict(start=0,end=1,text='百分之一'),dict(start=1,end=2,text='百的风险。'),
          dict(start=2,end=3,text='但不能忽略行业机会。')]
    units=P.editorial_sentence_units(cues)
    assert units==[dict(start=0,end=1,text='百分之一百的风险。'),
                   dict(start=2,end=2,text='但不能忽略行业机会。')]
    assert ''.join(x['text'] for x in units)==''.join(x['text'] for x in cues)


def test_nested_editorial_verdict_requires_real_claim_and_reason(monkeypatch,tmp_path):
    text='医药需求长期存在。因为人会衰老。所以我们长期关注。'
    cues=[dict(start=0,end=140,text=text)]
    analysis=dict(claim_quote='医药需求长期存在。',reasoning_quote='因为人会衰老。',
        conclusion_quote='所以我们长期关注。',opening_quote='医药需求长期存在。',
        ending_quote='所以我们长期关注。',summary='衰老带来持续需求',
        completeness_reason='观点、原因和收束均保留',audio_issues=[])
    verdict=dict(standalone_opening=True,complete_argument=True,reasoning_present=True,
        natural_ending=True,requires_audio_review=False)
    def review(*args,**options):
        assert sorted(options['response_schema']['properties'])==['analysis','verdict']
        return json.dumps(dict(analysis=analysis,verdict=verdict))
    monkeypatch.setattr(P,'llm',review)
    proof=P.review_complete_argument(cues,[dict(start=0,end=0)],'林园','',tmp_path,'')
    assert proof['reasoning_quote']=='因为人会衰老。' and proof['issues']==[]
    (tmp_path/'editorial_review.json').unlink()
    analysis['reasoning_quote']=''
    with pytest.raises(P.EditorialReviewUnavailable,match='没有原文理由证据'):
        P.review_complete_argument(cues,[dict(start=0,end=0)],'林园','',tmp_path,'')


def test_caption_sentence_path_rejoins_display_rows_without_model(monkeypatch,tmp_path):
    from presentation import layout_for
    entries=[dict(start_sec=0,end_sec=2,zh='科技股的长期风险'),
             dict(start_sec=2,end_sec=4,zh='需要考虑。'),
             dict(start_sec=4,end_sec=7,zh='因为会有新的技术替代。')]
    monkeypatch.setattr(P,'llm',lambda *a,**kw:pytest.fail('source sentences called model'))
    result=P.semantic_caption_entries(entries,'',layout_for(720,1280,True),tmp_path/'captions.json')
    assert ''.join(g['zh'] for g in result)=='科技股的长期风险需要考虑因为会有新的技术替代'
    assert all(g['semantic_group'] and g['end_sec']-g['start_sec']<=8 for g in result)


def test_explicit_budget_still_bounds_local_request(monkeypatch,tmp_path):
    monkeypatch.setattr(P,'TEXT_BACKEND','local')
    monkeypatch.setattr(P,'BASE',tmp_path)
    def fail(request,timeout):
        assert 0<timeout<=2
        raise TimeoutError()
    monkeypatch.setattr(P.urllib.request,'urlopen',fail)
    with pytest.raises(P.LocalTextUnavailable):
        P.llm([{'role':'user','content':'review'}],'',budget_sec=2)


def test_actual_single_selection_object_is_normalized_without_weakening_duration(monkeypatch,tmp_path):
    response=dict(start=0,end=43,score=7,reason='科技股长期投资风险')
    monkeypatch.setattr(P,'llm',lambda *a,**kw:json.dumps(response))
    cues=[dict(start=i*3,end=(i+1)*3,text='真实字幕') for i in range(50)]
    picks=P.pick_highlights(cues,'林园','',tmp_path)
    assert picks==[response]
    assert P.editorial.range_seconds(cues,picks[0])==132
    short=P.parse_llm_json_array(json.dumps(dict(response,end=3)))[0]
    with pytest.raises(ValueError,match='120'):
        P.editorial.range_seconds(cues,short)


def test_invalid_model_shape_cannot_turn_into_empty_content_verdict(monkeypatch,tmp_path):
    monkeypatch.setattr(P,'llm',lambda *a,**kw:'{"unexpected":"shape"}')
    cues=[dict(start=i*3,end=(i+1)*3,text='真实字幕') for i in range(50)]
    with pytest.raises(P.LocalTextUnavailable,match='格式无效'):
        P.pick_highlights(cues,'林园','',tmp_path)
    assert not (tmp_path/'highlights.json').exists()


def test_local_text_backend_rejects_remote_endpoint(monkeypatch,tmp_path):
    monkeypatch.setattr(P,'TEXT_BACKEND','local')
    monkeypatch.setattr(P,'LOCAL_LLM_URL','https://api.example.com/chat')
    monkeypatch.setattr(P,'BASE',tmp_path)
    with pytest.raises(RuntimeError,match='只允许本机回环地址'):
        P.llm([{'role':'user','content':'x'}],'')


def test_local_text_backend_forwards_json_schema(monkeypatch,tmp_path):
    monkeypatch.setattr(P,'TEXT_BACKEND','local')
    monkeypatch.setattr(P,'LOCAL_LLM_URL','http://127.0.0.1:11434/api/chat')
    monkeypatch.setattr(P,'BASE',tmp_path)
    schema={'type':'object','properties':{'passed':{'type':'boolean'}},
            'required':['passed'],'additionalProperties':False}
    class Reply:
        def __enter__(self):return self
        def __exit__(self,*args):return False
        def read(self):return b'{"message":{"content":"{\\"passed\\":true}"}}'
    def open_local(request,timeout):
        assert json.loads(request.data)['format']==schema
        return Reply()
    monkeypatch.setattr(P.urllib.request,'urlopen',open_local)
    assert json.loads(P.llm([{'role':'user','content':'x'}],'',response_schema=schema))=={'passed':True}


def _load_fc():
    spec = importlib.util.spec_from_file_location("quality_fc", ROOT / "linyuan/fc/index.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


FC = _load_fc()


def test_resolution_gate_checks_short_edge_after_crop(monkeypatch):
    monkeypatch.setattr(P, "video_size", lambda src: (854, 479))
    with pytest.raises(P.VisualQualityError, match="短边 479 < 480"):
        P.ensure_min_short_edge(Path("final.mp4"), label="裁切后成片")

    monkeypatch.setattr(P, "video_size", lambda src: (854, 480))
    assert P.ensure_min_short_edge(Path("final.mp4")) == (854, 480)


def test_workflow_rejects_source_below_480():
    workflow = (ROOT / ".github/workflows/linyuan-produce-cn.yml").read_text()
    assert P.MIN_SHORT_EDGE == 480
    assert FC.MIN_SHORT_EDGE == 480
    assert "default: true" in workflow
    assert P.SOURCE_MIN_DURATION == 120
    assert FC.MIN_DUR == 120
    assert "--source-check-only" in workflow
    assert workflow.index("name: 素材质量门禁") < workflow.index("name: 出片")


def test_publish_boundary_removes_urls_from_desc_and_source_field():
    desc = "观点摘要。\n原片：https://www.bilibili.com/video/BV123\n更多 t.cn/abc"
    cleaned = FC.clean_publish_desc(desc)
    assert "http" not in cleaned
    assert "t.cn" not in cleaned
    label = FC.publication_source_label({
        "source": "bilibili_search",
        "source_url": "https://www.bilibili.com/video/BV123",
    })
    assert label == "公开访谈资料（哔哩哔哩）"
    assert "http" not in label


def test_transcript_fingerprint_survives_light_rewrite():
    a = ("科技股长期来看风险很高，但是人工智能确实可能带来革命性的行业机会。"
         "我们不能因为机会就忽视风险。")
    b = ("科技股长期来看风险很高，但人工智能可能带来革命性行业机会。"
         "我们不能因机会忽视风险。")
    other = "慢性病患者会随着人口老龄化增加，医药企业的需求是长期的。"
    fa = {"transcript_ngrams": P.transcript_ngram_fingerprints(a)}
    fb = {"transcript_ngrams": P.transcript_ngram_fingerprints(b)}
    fc = {"transcript_ngrams": P.transcript_ngram_fingerprints(other)}
    assert "转写片段重合" in FC.fingerprint_duplicate(fa, fb)
    assert FC.fingerprint_duplicate(fa, fc) is None


def test_composite_media_fingerprint_requires_audio_and_video():
    current = {"video_dhash": ["0" * 16] * 8,
               "audio_chromaprint": ["0" * 8] * 6}
    same = {"video_dhash": ["0" * 16] * 8,
            "audio_chromaprint": ["0" * 8] * 6}
    only_same_face = {"video_dhash": ["0" * 16] * 8,
                      "audio_chromaprint": ["f" * 8] * 6}
    assert "音频" in FC.fingerprint_duplicate(current, same)
    assert FC.fingerprint_duplicate(current, only_same_face) is None


def test_topic_cooldown_blocks_rephrased_idea_for_14_days():
    now = time.time()
    state = {"published": {"old": {
        "bvid": "BV-old", "ts": now - 2 * 86400,
        "title": "林园：科技股100%风险但AI革命性机会｜林园",
    }}}
    hit = FC.find_recent_topic(
        "林园：科技股100%风险？过去确实如此｜林园", state, now=now)
    assert hit and hit["bvid"] == "BV-old"
    assert FC.find_recent_topic(
        "林园：医学进步救不了衰老，我只投这些｜林园", state, now=now) is None

    state["published"]["old"]["ts"] = now - 15 * 86400
    assert FC.find_recent_topic(
        "林园：科技股100%风险？过去确实如此｜林园", state, now=now) is None


def test_topic_cooldown_does_not_block_other_parts_of_same_master():
    now = time.time()
    state = {"published": {"master": {
        "bvid": "BV-part-1", "ts": now - 60,
        "title": "林园：老龄化医药未来30年有100倍机会",
    }}}
    title = "林园：老龄化医药未来30年，我认为至少有100倍机会"
    assert FC.find_recent_topic(title, state, now=now)
    assert FC.find_recent_topic(
        title, state, now=now, exclude_slug="master") is None


def test_picker_applies_topic_cooldown_before_dispatch():
    now = time.time()
    state = {
        "dispatched": [], "rejected": [], "pending_retry": [],
        "published": {"old": {
            "bvid": "BV-old", "ts": now - 86400,
            "title": "林园：科技股100%风险但AI革命性机会｜林园",
        }},
    }
    items = [
        {"id": "repeat", "title": "林园：科技股100%风险？过去确实如此",
         "url": "https://example.com/repeat", "video_url": "https://cdn/repeat.mp4",
         "source": "weibo", "extra": {"duration": 180}},
        {"id": "fresh", "title": "林园：医学进步救不了衰老，我只投这些",
         "url": "https://example.com/fresh", "video_url": "https://cdn/fresh.mp4",
         "source": "weibo", "extra": {"duration": 180}},
    ]
    assert [x["key"] for x in FC.pick(items, state, 10)] == ["fresh"]


def test_skipped_duplicate_advances_part_without_joining_history(monkeypatch):
    monkeypatch.setattr(FC.time, "time", lambda: 123456)
    monkeypatch.setattr(FC, "save_state", lambda state: None)
    state = {"published": {}}
    dispatched = {"published_parts": 0}
    FC._record_skipped_part(
        state, dispatched, "new", {"name": "part-01.mp4"}, 2, 0,
        "重复主题", "与 BV-old 内容重复")

    assert dispatched["published_parts"] == 1
    assert state["published"]["new"]["parts"][0]["status"] == "skipped"
    assert list(FC.iter_published_parts(state)) == []


def _good_artifact_meta():
    return {
        "duration_sec": 150,
        "segments": [{"start":600,"end":750}],
        "editorial_review": {"version":FC.editorial.VERSION,"standalone_opening":True,
            "complete_argument":True,"reasoning_present":True,"natural_ending":True,
            "requires_audio_review":False,"summary":"完整观点和论据","transcript_sha256":"abc"},
        "quality_gate_version": FC.QUALITY_GATE_VERSION,
        "visual_standard_version": FC.VISUAL_STANDARD_VERSION,
        "cover_standard_version": FC.COVER_STANDARD_VERSION,
        "cover_person_image_verified": True,
        "cover_person_image_source": "authority_reference",
        "title": "林园：医药行业未来三十年会有长期机会",
        "title_quality_verified": True,
        "review_assets_verified": True,
        "preview_30s": "preview_30s.mp4",
        "contact_sheet_6": "contact_sheet_6.jpg",
        "layout_proof": {
            "live_region": {"x": 44, "y": 360, "width": 632, "height": 470},
            "subtitle_region": {"x": 38, "y": 874, "width": 644, "height": 166},
            "subtitle_max_lines": 2,
            "subtitle_font_px": 40,
            "subtitle_vertical_alignment": "center",
            "subtitle_layout_version": 2,
        },
        "speaker": "林园",
        "visual_identity": {
            "speaker": "林园", "same_person_frames": [1, 2],
            "confidence": 0.91,
        },
        "resolution": {"width": 854, "height": 480, "short_edge": 480},
        "watermark_verified": True,
        "live_region_verified": True,
        "no_qr_verified": True,
        "subtitle_semantic_groups_verified": True,
        "subtitle_files": ["subtitles.ass"],
        "subtitle_text_sha256": "b" * 64,
        "no_black_bars_verified": True,
        "brand_watermark_applied": True,
        "has_existing_subtitles": False,
        "subtitles_burned": True,
        "clean_strategy": "direct",
        "clean_filter_verified": True,
        "fingerprints": {
            "sha256": "a" * 64,
            "video_dhash": ["0" * 16] * 4,
            "audio_chromaprint": ["0" * 8] * 4,
            "transcript_ngrams": ["1" * 16] * 8,
        },
    }


def test_old_artifact_without_new_quality_proof_is_rejected():
    assert "旧成片" in FC.artifact_quality_error({"speaker": "林园"})
    assert FC.artifact_quality_error(_good_artifact_meta()) is None


def test_review_paused_batch_cannot_publish(monkeypatch):
    def should_not_load_state():
        raise AssertionError("paused batch must stop before reading the queue")

    monkeypatch.setattr(FC, "load_state", should_not_load_state)
    result = FC.publish_handler({
        "batch_slug": "ly-parity-v3-14-0905",
        "ignore_daily_limit": True,
        "force_publish": True,
    })
    assert result == {"published": 0, "review_paused": 1,
                      "slug": "ly-parity-v3-14-0905"}


@pytest.mark.parametrize(("field", "value", "message"), [
    ("resolution", {"short_edge": 479}, "短边 479"),
    ("visual_standard_version", 2, "v3 对标视觉标准"),
    ("cover_standard_version", 3, "v4 真人图强制标准"),
    ("cover_person_image_verified", False, "已核验真人图"),
    ("title_quality_verified", False, "标题没有通过"),
    ("review_assets_verified", False, "30秒预览"),
    ("watermark_verified", False, "角标复检"),
    ("live_region_verified", False, "真人动态区"),
    ("no_qr_verified", False, "二维码复检"),
    ("no_black_bars_verified", False, "黑边/取景复检"),
    ("subtitle_semantic_groups_verified", False, "完整意群字幕"),
    ("brand_watermark_applied", False, "品牌水印"),
    ("fingerprints", {}, "指纹不完整"),
    ("has_existing_subtitles", True, "内嵌字幕"),
    ("subtitles_burned", False, "统一字幕"),
    ("clean_strategy", "", "干净画面策略"),
    ("clean_filter_verified", False, "清理方案未经复检"),
])
def test_artifact_quality_proof_fails_closed(field, value, message):
    meta = _good_artifact_meta()
    meta[field] = value
    assert message in FC.artifact_quality_error(meta)


def test_ocr_subtitle_band_detects_persistent_double_subtitle_risk(monkeypatch):
    cov = [0.0] * 100
    cov[73:78] = [0.75] * 5
    monkeypatch.setattr(P, "ocr_row_coverage", lambda *args, **kwargs: cov)
    assert P.has_existing_subtitles(Path("blue-band-white-text.mp4")) is True


def test_ocr_detects_persistent_top_editorial_card(monkeypatch):
    cov = [0.0] * 100
    cov[2:17] = [1.0] * 15
    monkeypatch.setattr(P, "ocr_row_coverage", lambda *args, **kwargs: cov)
    assert P.has_existing_subtitles(Path("persistent-top-title.mp4")) is True


def test_ocr_leaves_small_top_logo_for_delogo(monkeypatch):
    cov = [0.0] * 100
    cov[3:8] = [1.0] * 5
    monkeypatch.setattr(P, "ocr_row_coverage", lambda *args, **kwargs: cov)

    class ClosedCapture:
        def isOpened(self):
            return False

    import cv2
    monkeypatch.setattr(cv2, "VideoCapture", lambda *args: ClosedCapture())
    assert P.has_existing_subtitles(Path("small-top-logo.mp4")) is False


def test_ocr_subtitle_band_ignores_sporadic_lower_screen_text(monkeypatch):
    cov = [0.0] * 100
    cov[73:78] = [0.25] * 5
    monkeypatch.setattr(P, "ocr_row_coverage", lambda *args, **kwargs: cov)

    class ClosedCapture:
        def isOpened(self):
            return False

    import cv2
    monkeypatch.setattr(cv2, "VideoCapture", lambda *args: ClosedCapture())
    assert P.has_existing_subtitles(Path("clean-interview.mp4")) is False


def test_source_gate_rebuilds_embedded_subtitles_as_audio_card(monkeypatch,
                                                               tmp_path):
    src = tmp_path / "source.mp4"
    src.write_bytes(b"video")
    monkeypatch.setattr(P, "_file_sha256", lambda path: "source-hash")
    monkeypatch.setattr(P, "probe", lambda *args, **kwargs: "120")
    monkeypatch.setattr(P, "ensure_min_short_edge", lambda *args, **kwargs: (854, 480))
    monkeypatch.setattr(P, "has_existing_subtitles", lambda path: True)
    monkeypatch.setattr(P, "verify_source_identity", lambda *args, **kwargs: {
        "speaker": "林园", "same_person_frames": [1, 2], "confidence": 0.9,
    })
    monkeypatch.setattr(P, "build_clean_source_plan", lambda *args, **kwargs: {
        "clean_strategy": "audio_card",
        "clean_video_filter": "",
        "clean_output_resolution": {
            "width": 1280, "height": 720, "short_edge": 720,
        },
        "clean_filter_verified": True,
        "detected_corner_logos": [],
    })

    report = P.run_source_quality_gate(
        src, tmp_path / "work", "林园", "sk-test", tmp_path / "report.json")
    assert report["passed"] is True
    assert report["raw_has_existing_subtitles"] is True
    assert report["has_existing_subtitles"] is False
    assert report["clean_strategy"] == "audio_card"


def test_source_gate_rejects_duration_before_visual_checks(monkeypatch, tmp_path):
    src = tmp_path / "short.mp4"
    src.write_bytes(b"video")
    monkeypatch.setattr(P, "_file_sha256", lambda path: "source-hash")
    monkeypatch.setattr(P, "probe", lambda *args, **kwargs: "43")
    monkeypatch.setattr(
        P, "ensure_min_short_edge",
        lambda *args, **kwargs: pytest.fail("短片应先被时长门禁淘汰"))
    report = P.run_source_quality_gate(src, tmp_path, "林园", "sk-test")
    assert report["passed"] is False
    assert "43s" in report["reason"]


def test_obsolete_source_cache_runs_current_gate_and_writes_actual_rejection(monkeypatch, tmp_path):
    src = tmp_path / 'source.mp4'
    src.write_bytes(b'video')
    def stale(*args):
        raise P.VisualQualityError('素材质检报告版本过旧')
    monkeypatch.setattr(P, 'verified_source_evidence', stale)
    monkeypatch.setattr(P, 'probe', lambda *args, **kwargs: '43')
    report = P.run_source_quality_gate(src, tmp_path, '林园', 'test')
    assert report['passed'] is False
    assert '43s' in report['reason']
    assert report['quality_gate_version'] == P.QUALITY_GATE_VERSION
    assert json.loads((tmp_path/'source_quality.json').read_text()) == report


def test_source_report_is_reused_only_for_the_same_media(monkeypatch, tmp_path):
    src = tmp_path / "source.mp4"
    src.write_bytes(b"video")
    report = tmp_path / "source_quality.json"
    report.write_text(json.dumps({
        "quality_gate_version": P.QUALITY_GATE_VERSION,
        "source_sha256": "right",
        "passed": True,
        "has_existing_subtitles": False,
        "clean_strategy": "direct",
        "clean_filter_verified": True,
        "clean_output_resolution": {
            "width": 854, "height": 480, "short_edge": 480,
        },
        "visual_identity": {"speaker": "林园"},
        "resolution": {"width": 854, "height": 480, "short_edge": 480},
    }), encoding="utf-8")
    monkeypatch.setattr(P, "_file_sha256", lambda path: "right")
    assert P.load_source_quality_report(src, report)["passed"] is True
    monkeypatch.setattr(P, "_file_sha256", lambda path: "different")
    with pytest.raises(P.VisualQualityError, match="不匹配"):
        P.load_source_quality_report(src, report)


def test_workflow_runs_source_gate_before_asr_setup_and_uploads_rejection():
    workflow = (ROOT / ".github/workflows/linyuan-produce-cn.yml").read_text()
    assert workflow.index("name: 素材质量门禁") < workflow.index("name: 安装出片依赖")
    assert "--source-check-only" in workflow
    assert "source-reject-${{ env.RUN_SLUG }}" in workflow


def test_fc_consumes_source_rejection_artifact(monkeypatch):
    state = {"dispatched": [{
        "slug": "ly-bad", "key": "source:bad", "video_id": "bad",
        "source_url": "https://example.test/bad", "ts": 1,
    }, {
        "slug": "ly-bad", "key": "source:bad", "video_id": "bad",
        "source_url": "https://example.test/bad", "ts": 2,
    }], "rejected": [], "pending_retry": [
        {"key": "source:bad", "page_url": "https://example.test/bad"},
        {"key": "source:good", "page_url": "https://example.test/good"},
    ]}

    def fake_gh(method, path, *args, **kwargs):
        if "/runs?status=completed" in path:
            return {"workflow_runs": [{"id": 11}]}
        if path == "/actions/runs/11/artifacts":
            return {"artifacts": [{
                "id": 22, "name": "source-reject-ly-bad",
                "archive_download_url": "https://example.test/reject.zip",
                "expired": False,
            }]}
        if method == "DELETE" and path == "/actions/artifacts/22":
            return {}
        raise AssertionError((method, path))

    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as archive:
        archive.writestr("source_quality.json", json.dumps({
            "passed": False, "reason": "源视频含持续内嵌字幕",
        }))

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self, limit):
            return payload.getvalue()

    monkeypatch.setattr(FC, "gh", fake_gh)
    monkeypatch.setattr(FC, "download_reviewed_zip", lambda aid,path,**kwargs: Path(path).write_bytes(payload.getvalue()))
    monkeypatch.setattr(FC, "log_event", lambda *args, **kwargs: None)
    assert FC._collect_source_rejections(state) == 1
    assert all(row["failed"] is True for row in state["dispatched"])
    assert "内嵌字幕" in state["rejected"][0]["error"]
    assert [row["key"] for row in state["pending_retry"]] == ["source:good"]


def test_dispatch_consumes_terminal_rejections_before_picking(monkeypatch):
    state = {
        "dispatched": [], "rejected": [], "pending_retry": [],
        "published": {}, "daily_publish": {},
    }
    monkeypatch.setattr(FC, "load_state", lambda: state)
    monkeypatch.setattr(FC, "_collect_source_rejections", lambda current: 1)
    saved = []
    monkeypatch.setattr(FC, "save_state", lambda current: saved.append(current.copy()))
    # Admission now stops on actual reserve, not stale job placeholders. The
    # lease transport is covered separately; this check exercises ordering.
    monkeypatch.setattr(FC, "source_inventory", lambda current: {"daily_mix_usable": FC.TARGET_READY_RESERVE,
                                                               "verified_landscape": FC.TARGET_LANDSCAPE_RESERVE})

    assert FC._dispatch_admitted()["reserve_full"] == 1
    assert saved


def test_pending_inventory_counts_latest_slug_state_once(monkeypatch):
    monkeypatch.setattr(FC.time, "time", lambda: 10_000)
    state = {"dispatched": [
        {"slug": "same", "ts": 9_000,
         "production_rules_version": FC.PRODUCTION_RULES_VERSION},
        {"slug": "same", "ts": 9_500,
         "production_rules_version": FC.PRODUCTION_RULES_VERSION},
    ], "published": {}}
    assert FC._pending_final_count(state) == 1

    state["dispatched"].append({
        "slug": "same", "ts": 9_700, "failed": True,
        "production_rules_version": FC.PRODUCTION_RULES_VERSION,
    })
    assert FC._pending_final_count(state) == 0
    assert FC._latest_dispatches(state) == [state["dispatched"][-1]]


def test_rejection_refills_slot_cleans_temp_and_aggregates_result(monkeypatch,
                                                                  tmp_path):
    monkeypatch.setattr(
        FC, "publish_handler",
        lambda event, context: {"published": 1, "skipped": 1})
    extracted = tmp_path / "extracted"
    extracted.mkdir()
    (extracted / "large.mp4").write_bytes(b"video")
    result = FC._continue_after_rejection(
        {"_attempted_slugs": ["old"]}, None, "bad",
        {"published": 0, "quality_rejected": 1}, extracted)
    assert result == {"published": 1, "skipped": 1, "quality_rejected": 1}
    assert not extracted.exists()


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="需要 ffmpeg")
def test_reencoded_video_keeps_composite_fingerprint(tmp_path):
    original = tmp_path / "original.mp4"
    encoded = tmp_path / "encoded.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "lavfi", "-i", "testsrc2=size=640x480:rate=10",
        "-f", "lavfi", "-i", "sine=frequency=880:sample_rate=48000",
        "-t", "4", "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac", str(original)], check=True)
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error", "-i", str(original),
        "-c:v", "libx264", "-crf", "27", "-c:a", "aac", "-b:a", "96k",
        str(encoded)], check=True)
    a = P.build_content_fingerprints(original, "完全不同的测试文本甲")
    b = P.build_content_fingerprints(encoded, "完全不同的测试文本乙")
    assert "音频" in FC.fingerprint_duplicate(a, b)


def test_partial_qr_blocks_old_live_artifacts():
    meta = _good_artifact_meta()
    meta['render_mode'] = 'live_video_card'
    assert '残缺二维码' in FC.artifact_quality_error(meta)
    meta['partial_qr_verified'] = True
    assert '完整人脸' in FC.artifact_quality_error(meta)
    meta['full_face_frames'] = 6
    assert '林园本人' in FC.artifact_quality_error(meta)
    meta['final_live_identity'] = dict(speaker='林园',sample_count=6,
        same_person_frames=[1,2,3,4,5],confidence=.95,watermark_texts=[],
        motion=dict(version=2026091201,passed=True))
    assert FC.artifact_quality_error(meta) is None


def test_actual_ass_is_read_and_asr_corruption_is_rejected(tmp_path):
    meta = _good_artifact_meta()
    ass = tmp_path / "subtitles.ass"
    ass.write_text("[Events]\nDialogue: 0,0:00:00.00,0:00:03.00,Default,,0,0,0,,"
                   "{\\an5}你买片公司万丈深渊\n", encoding="utf-8")
    text = FC.editorial.subtitle_files_text(tmp_path, meta["subtitle_files"])
    meta["subtitle_text_sha256"] = FC.editorial.text_digest(text)
    assert "ASR污染" in FC.artifact_subtitle_error(meta, tmp_path)


def test_actual_ass_rejects_qg12_verified_corruption(tmp_path):
    text = "这个当然它也会受到多体但是我们看他的比如说它会很快恢复"
    ass = "Dialogue: 0,0:00:00.00,0:00:03.00,Default,,0,0,0,," + text
    (tmp_path / "bad.ass").write_text(ass, encoding="utf-8")
    meta = {
        "subtitle_files": ["bad.ass"],
        "subtitle_text_sha256": FC.editorial.text_digest(text),
    }
    assert "ASR污染" in FC.artifact_subtitle_error(meta, tmp_path)


def test_actual_ass_hash_cannot_be_forged_by_boolean(tmp_path):
    meta = _good_artifact_meta()
    (tmp_path / "subtitles.ass").write_text(
        "Dialogue: 0,0:00:00.00,0:00:03.00,Default,,0,0,0,,完整观点\n",
        encoding="utf-8")
    assert "指纹不一致" in FC.artifact_subtitle_error(meta, tmp_path)

def test_cropped_qr_finder_is_detected_without_decodable_full_code():
    import cv2
    import numpy as np
    finder = np.zeros((7, 7), dtype=np.uint8)
    finder[1:6, 1:6] = 255
    finder[2:5, 2:5] = 0
    frame = np.full((470, 632), 180, dtype=np.uint8)
    frame[-42:-14, -42:-14] = cv2.resize(finder, (28, 28), interpolation=cv2.INTER_NEAREST)
    assert P.partial_qr_finder_score(frame) >= .70
    assert P.partial_qr_finder_score(np.full_like(frame, 180)) < .70
