"""CPU proof reuse must not turn a model pass flag into a gate exemption."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan'))
import reuse_title_verification as reuse
import title_rewrite as T

ROOT=Path(__file__).resolve().parents[1]


def fixture_rows():
    rows=[]
    specs=[('linyuan_0913_title.json','龙头','林园：龙头未定，先配置可能成为龙头的公司','龙头未定，如何配置公司'),
           ('linyuan_0913_landscape_title.json','高端消费','林园：高端消费需求旺盛，企业经营压力不大','高端消费需求是否依旧旺盛')]
    for fixture,subject,title,cover in specs:
        source=''.join(c['text'] for c in json.loads((ROOT/'tests/fixtures'/fixture).read_text())['cues'])
        item=dict(title=title,cover_title=cover,subject=subject,evidence=[source])
        package=T._package(item,source,dict(method='cpu_text_review',appeal=4,
            reason='单元测试构造的审核证明，用于检验精确来源绑定和不可篡改规则',
            **{k:True for k in T.CHECKS}),[item,item,item])
        rows.append(dict(fixture=fixture,passed=True,**package))
    return rows


def test_only_matching_complete_source_bound_cpu_results_are_reusable():
    rows=fixture_rows();reuse.validate_results(rows,ROOT)
    for mutate in (lambda r:r.pop(),lambda r:r[0].update(passed=False),
                   lambda r:r[0]['title_rewrite'].update(source_sha256='0'*64),
                   lambda r:r[0].update(title=r[0]['title']+'保证赚钱'),
                   lambda r:r[0]['title_rewrite']['review'].update(method='source_quote'),
                   lambda r:r[0]['title_rewrite']['review'].update(attribution_correct=False)):
        changed=deepcopy(rows);mutate(changed)
        with pytest.raises(ValueError):reuse.validate_results(changed,ROOT)


def test_actual_known_host_hypothesis_is_rejected_even_with_true_cpu_flags():
    rows=fixture_rows();row=rows[1]
    source=row['title_rewrite']['evidence'][0]
    item=dict(title='林园：高端消费表现好，但一线白酒新品市场反馈未达预期？',
              cover_title='高端消费表现好，但新品市场反馈如何？',subject='高端消费',evidence=[source])
    package=T._package(item,source,dict(method='cpu_text_review',appeal=5,
        reason='这是旧真实模型曾错误通过的主持人假设，不能作为嘉宾结论',
        **{k:True for k in T.CHECKS}),[item,item,item])
    rows[1]={**row,**package}
    with pytest.raises(ValueError,match='host hypothesis'):reuse.validate_results(rows,ROOT)


def test_workflow_wrapper_never_erases_changed_cpu_test_rules():
    original='jobs:\n  check:\n    run: assert source_matches\n'
    wrapped='actions: read # TITLE_REUSE_GUARD\n# TITLE_REUSE_BEGIN\ncache_wrapper\n# TITLE_REUSE_END\n'+original
    assert reuse.canonical_workflow(wrapped)==reuse.canonical_workflow(original)
    assert reuse.canonical_workflow(wrapped.replace('assert source_matches','assert True'))!=reuse.canonical_workflow(original)
    with pytest.raises(ValueError):reuse.canonical_workflow('# TITLE_REUSE_BEGIN\nrest')


def test_model_must_match_actual_cpu_response_evidence(tmp_path):
    folder=tmp_path/'linyuan/.llm_cache';folder.mkdir(parents=True)
    path=folder/'response.json'
    with pytest.raises(ValueError,match='No actual'):reuse.model_evidence(tmp_path,'qwen3:8b')
    path.write_text(json.dumps(dict(backend='local',model='qwen3:8b',content='actual result')))
    assert reuse.model_evidence(tmp_path,'qwen3:8b')==[path]
    with pytest.raises(ValueError,match='model differs'):reuse.model_evidence(tmp_path,'qwen3.5:9b')
