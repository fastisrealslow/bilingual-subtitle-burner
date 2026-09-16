import importlib.util
import json
from pathlib import Path
import sys

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'linyuan'))
import simulate_sources as sim


def test_manifest_has_100_different_fixed_sources():
    manifest = sim.read(sim.MANIFEST)
    samples = sim.validate_manifest(manifest)
    assert len(samples) == 100
    assert manifest['pool_count'] >= 100
    assert {r['platform'] for r in samples} >= {'weibo_search', 'bilibili_search', 'yicai_video'}


def test_sample_reproducible_independent_of_pool_order():
    pool = [dict(page_url=f'https://www.bilibili.com/video/BV{i:010d}', key=str(i)) for i in range(150)]
    assert sim.sample(pool) == sim.sample(list(reversed(pool)))
    assert len(sim.sample(pool + pool)) == 100


def test_first_success_does_not_skip_failures_or_fake_acceptance():
    responses = iter([ValueError('bad picture'), None, {'final':'real.mp4'}])
    def actual(*args, **kw):
        result = next(responses)
        if isinstance(result, Exception): raise result
        return result
    wrapper = sim.FirstSuccess(actual)
    with pytest.raises(ValueError): wrapper()
    assert not wrapper.accepted
    assert wrapper() is None
    assert not wrapper.accepted
    assert wrapper() == {'final':'real.mp4'}
    assert wrapper() is None
    assert wrapper.calls == 3
    assert wrapper.skipped == 1


def test_missing_reports_remain_in_100_denominator():
    manifest = sim.read(sim.MANIFEST)
    s = manifest['samples']
    reports = [dict(sample=s[0],status='passed',stage='final-accepted',finals=[{}]),
               dict(sample=s[1],status='rejected',stage='source-quality',finals=[])]
    result = sim.aggregate(manifest, reports)
    assert (result['passed'],result['rejected'],result['unresolved']) == (1,1,98)
    assert result['confirmed_success_percent'] == 1
    assert result['possible_success_percent_range'] == [1,99]
    assert result['resolved_success_percent'] == 50
    assert not result['complete']
    with pytest.raises(ValueError): sim.aggregate(manifest, reports+reports)


def test_runtime_failure_is_not_quality_rejection():
    args = dict(finals=[],validation_error='',source={},execution={},steps={})
    assert sim.classify(batch={'rejected':[dict(error_type='RuntimeError',retryable=False)]},**args)[0] == 'unresolved'
    assert sim.classify(batch={'rejected':[dict(error_type='VisualQualityError',retryable=False)]},**args)[0] == 'rejected'
    assert sim.classify(batch={'rejected':[dict(error_type='VisualQualityError',retryable=True)]},**args)[0] == 'unresolved'
    assert sim.classify(batch={},**dict(args,source={'passed':False,'retryable':False}))[0] == 'rejected'
    assert sim.classify(batch={},**dict(args,finals=[{'file':'accepted.mp4'}]))[0] == 'passed'


def test_workflow_cannot_publish_or_mutate_production():
    path=ROOT/'.github/workflows/linyuan-simulate-100.yml'
    raw=path.read_text()
    workflow=yaml.safe_load(raw)
    assert workflow['permissions']=={'contents':'read','actions':'read'}
    assert workflow['on']['push']['paths']==['linyuan/simulations/source100-20260916.json']
    assert workflow['name'] != '中文源出片'
    assert workflow['jobs']['simulate']['strategy']['max-parallel'] <= 12
    assert workflow['jobs']['simulate']['strategy']['fail-fast'] is False
    for forbidden in ('publish_bilibili', 'action-gh-release', 'workflow run', 'repository_dispatch',
                      'git push', 'ALIBABA', 'ALICLOUD', 'actions/cache/save', 'actions/cache@'):
        assert forbidden not in raw
    for job in workflow['jobs'].values():
        assert 'permissions' not in job
        for step in job['steps']:
            if step.get('uses','').startswith('actions/upload-artifact@'):
                assert step['with']['name'].startswith('simulation-')
            assert 'secrets.' not in json.dumps(step) or step.get('id')=='src'
    simulate=workflow['jobs']['simulate']
    assert simulate['env']['CUDA_VISIBLE_DEVICES']==''
    render=next(s for s in simulate['steps'] if s.get('id')=='render')
    assert render['env']['TEXT_BACKEND']=='local'
    assert render['env']['SOURCE_EDITORIAL_FIRST']=='true'
    assert 'simulate_sources.py run' in render['run']
    assert set(workflow['on']['workflow_call']['inputs'])=={'sample_ids'}
    assert workflow['jobs']['summary']['if']=='${{ always() && !inputs.sample_ids }}'
    assert simulate['env']['SIMULATION_SAMPLE_IDS']=="${{ inputs.sample_ids || '' }}"


def test_diagnostic_matrix_retains_exact_fixed_source_records():
    manifest=sim.read(sim.MANIFEST)
    original=sim.validate_manifest(manifest)
    assert sim.matrix_samples(manifest)==original
    assert sim.matrix_samples(manifest,'87,5,9')==[r for r in original if r['id'] in (5,9,87)]
    for invalid in ('0','101','5,5','5,','5;echo bad','https://example.com'):
        with pytest.raises(ValueError):sim.matrix_samples(manifest,invalid)


def test_diagnostic_mode_cannot_emit_full_acceptance_summary(monkeypatch):
    monkeypatch.setenv('SIMULATION_SAMPLE_IDS','5,9')
    monkeypatch.setattr(sys,'argv',['simulate_sources.py','aggregate'])
    with pytest.raises(ValueError,match='diagnostic subset'):sim.main()
