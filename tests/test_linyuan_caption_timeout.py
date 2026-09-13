import json
import sys
from pathlib import Path
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan'))
import produce_cn as p
from presentation import layout_for
from restore_production_evidence import restore


def test_actual_818_pause_timing_does_not_retry_model_or_drop_a_word(monkeypatch,tmp_path):
    from caption_readability import clean_entries,display_payload_text
    raw=json.loads((Path(__file__).parent/'fixtures/linyuan_818_caption_timing.json').read_text())
    clean,_=clean_entries(raw)
    monkeypatch.setattr(p,'llm',lambda *a,**k:pytest.fail('Sub-frame timing difference must not retry the model'))
    out=p.semantic_caption_entries(raw,'',layout_for(720,1280,True),tmp_path/'captions.json')
    assert display_payload_text(''.join(e['zh'] for e in out))==display_payload_text(''.join(e['zh'] for e in clean))
    assert all(.25<=e['end_sec']-e['start_sec']<=8 for e in out)
    assert out[-1]['zh']=='这个退市倒闭的这些风险'
    assert out[-1]['end_sec']==pytest.approx(234.544)
    assert out[-1]['start_sec']==pytest.approx(226.544)
    assert p.caption_display_interval(0,8.11)==(0,8.11)  # Larger overruns still need a real split.


def test_caption_prompt_excludes_duplicate_timing_and_offset_tables(monkeypatch,tmp_path):
    class Captured(BaseException):pass
    entries=[dict(start_sec=i*3,end_sec=(i+1)*3,zh='守住现金流的企业大家还更愿意买',en='') for i in range(40)]
    def capture(messages,*args,**kwargs):
        prompt=messages[0]['content']
        assert len(prompt)<5000
        assert 'start_sec' not in prompt and '"end"' not in prompt
        assert 1000 < kwargs['max_tokens'] <= 4096
        assert kwargs['budget_sec'] <= 600
        assert kwargs['response_schema']['properties']['break_after_tokens']['items']['type']=='integer'
        raise Captured()
    monkeypatch.setattr(p,'llm',capture)
    monkeypatch.setattr(p,'source_caption_groups',lambda *a: (_ for _ in ()).throw(ValueError('Exercise model fallback')))
    with pytest.raises(Captured):p.semantic_caption_entries(entries,'',layout_for(720,1280,True),tmp_path/'captions.json')


def test_false_asr_period_after_function_word_is_reconnected(monkeypatch,tmp_path):
    entries=[dict(start_sec=0,end_sec=2,zh='他必须走这个。'),
             dict(start_sec=2,end_sec=5,zh='自主知识产权的方向。')]
    def forbidden(*args,**kwargs):raise AssertionError('Simple source repair called model')
    monkeypatch.setattr(p,'llm',forbidden)
    out=p.semantic_caption_entries(entries,'',layout_for(720,1280,True),tmp_path/'captions.json')
    assert ''.join(g['zh'] for g in out)=='他必须走这个自主知识产权的方向'


def test_recovery_matches_source_and_never_restores_approvals(tmp_path):
    old=tmp_path/'old';new=tmp_path/'new';old.mkdir();new.mkdir()
    for root in (old,new):
        (root/'source_quality.json').write_text(json.dumps(dict(source_sha256='a'*64,passed=True)))
    (old/'highlights_block_1.json').write_text('{"picks":[]}')
    (old/'source_identity.json').write_text('obsolete approval')
    restore(old,new)
    assert (new/'highlights_block_1.json').exists()
    assert not (new/'source_identity.json').exists()
    (new/'source_quality.json').write_text(json.dumps(dict(source_sha256='b'*64,passed=True)))
    with pytest.raises(ValueError,match='哈希不一致'):restore(old,new)


def test_invalid_model_boundaries_are_retryable_not_bad_source(monkeypatch,tmp_path):
    prompts=[]
    def invalid(messages,*args,**kwargs):
        prompts.append(messages[0]['content'])
        return '{"wrong_field":[]}'
    monkeypatch.setattr(p,'llm',invalid)
    monkeypatch.setattr(p,'source_caption_groups',lambda *a: (_ for _ in ()).throw(ValueError('Exercise model fallback')))
    entries=[dict(start_sec=0,end_sec=4,zh='我们长期持有优秀企业')]
    with pytest.raises(p.CaptionPlanningUnavailable):
        p.semantic_caption_entries(entries,'',layout_for(720,1280,True),tmp_path/'captions.json')
    assert len(set(prompts))==3  # Don't serve the same invalid answer from cache.
    assert issubclass(p.CaptionPlanningUnavailable,p.EditorialReviewUnavailable)
    assert not (tmp_path/'captions.json').exists()


def test_source_planner_preserves_negation_entities_and_timing(monkeypatch,tmp_path):
    entries=[dict(start_sec=0,end_sec=4,zh='我们不会因为短期波动卖出贵州茅台，'),
             dict(start_sec=4,end_sec=8,zh='股息率不到8%我不会买，'),
             dict(start_sec=8,end_sec=12,zh='因为现金流能够持续增长。')]
    monkeypatch.setattr(p,'llm',lambda *a,**k:pytest.fail('Source layout should not call model'))
    out=p.semantic_caption_entries(entries,'',layout_for(720,1280,True),tmp_path/'captions.json')
    clean=lambda s:p.re.sub(r'[\s，。！？；：、]','',s)
    assert ''.join(clean(g['zh']) for g in out)==clean(''.join(e['zh'] for e in entries))
    assert all(not g['zh'].endswith(('不会','不能','因为')) for g in out)
    assert any('贵州茅台' in g['zh'] for g in out)
    assert out[0]['start_sec']==0 and out[-1]['end_sec']<=12


def test_caption_schema_cannot_accept_one_screen_for_713_characters():
    schema=p.caption_break_schema(400,713,30)
    assert schema['properties']['break_after_tokens']['minItems']==24


def test_old_editorial_artifact_supplements_verified_titles_from_debug(monkeypatch,tmp_path):
    import restore_production_evidence as recovery
    work=tmp_path/'current';work.mkdir()
    source=dict(source_sha256='a'*64,passed=True)
    (work/'source_quality.json').write_text(json.dumps(source))
    (work/'cues_raw.json').write_text('current source-bound ASR')
    (work/'highlights_current.json').write_text('current selection')
    monkeypatch.setattr(sys,'argv',['restore','--run-id','801','--work',str(work)])
    monkeypatch.setattr(recovery.subprocess,'check_output',lambda *a:json.dumps(dict(artifacts=[
        dict(name='editorial-old',expired=False),dict(name='debug-old',expired=False)])).encode())
    names=[]
    def download(command,**kwargs):
        name=command[command.index('--name')+1];names.append(name)
        root=Path(command[command.index('--dir')+1])/'_tmp';root.mkdir()
        (root/'source_quality.json').write_text(json.dumps(source))
        if name=='debug-old':
            (root/'copywrite_2.json').write_text('verified-title-evidence')
            (root/'highlights_current.json').write_text('older selection must not overwrite')
            (root/'cues_raw.json').write_text('older ASR must not overwrite')
    monkeypatch.setattr(recovery.subprocess,'run',download)
    recovery.main()
    assert names==['editorial-old','debug-old']
    assert (work/'copywrite_2.json').read_text()=='verified-title-evidence'
    assert (work/'cues_raw.json').read_text()=='current source-bound ASR'
    assert (work/'highlights_current.json').read_text()=='current selection'


def test_fetch_only_failure_is_cache_miss_after_fresh_source_passes(tmp_path):
    old=tmp_path/'old';work=tmp_path/'work';old.mkdir();work.mkdir()
    fresh=dict(passed=True,source_sha256='b'*64)
    (work/'source_quality.json').write_text(json.dumps(fresh))
    (old/'source_quality.json').write_text(json.dumps(dict(passed=False,retryable=True,
        failure_stage='source-fetch',reason='FetchBudgetExceeded')))
    (old/'copywrite.json').write_text('must never import from incomplete source')
    assert restore(old,work) is False
    assert not (work/'copywrite.json').exists()
    assert json.loads((work/'source_quality.json').read_text())==fresh
    (old/'source_quality.json').write_text(json.dumps(dict(passed=True,source_sha256='a'*64)))
    with pytest.raises(ValueError,match='哈希不一致'):restore(old,work)
    (work/'source_quality.json').write_text(json.dumps(dict(passed=False,source_sha256='b'*64)))
    with pytest.raises(ValueError,match='本次母片尚未'):restore(old,work)
