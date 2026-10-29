"""Revalidate the actual CPU run after its final full-name-only harness error."""
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'linyuan'))
import produce_cn as p
import publishing_experiment as E
from reuse_title_verification import validate_candidate_pool
from title_rewrite import error


def test_real_cpu_responses_match_every_full_prompt_and_schema_before_ordinary_title_validation(tmp_path, monkeypatch):
    root=Path(__file__).with_name('fixtures')
    fixture=json.loads((root/'linyuan_story_title_20261029.json').read_text())
    cpu=json.loads((root/'linyuan_story_title_cpu_20261029.json').read_text())
    assert E.digest(fixture)==cpu['fixture_sha256']
    assert cpu['cpu_run_id']==40902873470 and cpu['model']=='qwen3.5:9b'
    calls=iter(cpu['calls']);consumed=[]
    def replay(*args,**kwargs):
        row=next(calls)
        assert args[0]==row['prompt'], 'No changed context or speaker/claim instruction'
        assert kwargs.get('response_schema')==row['schema'], 'No weaker review schema'
        consumed.append(row)
        return row['response']
    monkeypatch.setattr(p,'llm',replay)
    monkeypatch.setattr(p,'LOCAL_LLM_MODEL',cpu['model'])
    monkeypatch.setenv('LINYUAN_EDITORIAL_PROFILE',E.TREATMENT)
    monkeypatch.setenv('LINYUAN_TITLE_DRAFT_PROFILE','production')
    units=fixture['source_cues'];cues=[dict(text=t,start=i,end=i+1) for i,t in enumerate(units)]
    result=p.copywrite(cues,list(range(len(cues))),'林园','访谈','',tmp_path,
        source_sha256=fixture['source_sha256'],story_evidence=fixture['story_evidence'])
    assert next(calls,None) is None and len(consumed)==3
    validate_candidate_pool(result)
    assert result['title_rewrite']['review']['method']=='cpu_text_review'
    assert not error(result['title'],result['title_rewrite'],''.join(units))
    assert not E.title_focus_error(result['title'],E.title_focus(fixture['story_evidence']),cover=result['cover_title'])
    assert any(name in result['title'] for name in E.SUBJECT_NAMES['招商银行'])
    # The actual model approved this changed price condition. Its true flags
    # must not let it survive the additional deterministic source check.
    unsafe=next(c for c in result['editorial_selection']['candidates'] if '股价翻倍' in c['title'])
    assert not unsafe['accepted'] and unsafe['programmatic_rejection']
    assert result['editorial_selection']['accepted_count']==1


def test_explicit_price_signal_is_not_an_arbitrary_doubling_signal():
    source='招商银行只有两块多啊，到十五块，你们去对应指数，那就是牛市开始。'
    assert E.price_signal_scope_error('林园：招行股价翻倍对应指数进入牛市','招行翻倍是牛市开始',source)
    assert not E.price_signal_scope_error('林园：招行涨到十五块就是牛市开始','招行到十五块是牛市起点',source)
    assert not E.price_signal_scope_error('股价翻倍是牛市开始','股价翻倍是牛市开始','我说股价翻倍就标志牛市开始。')
