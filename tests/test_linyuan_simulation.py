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


def test_library_sample_is_bound_to_all_pending_sources_and_keeps_20_denominator():
    import copy
    manifest=sim.read(ROOT/'linyuan/simulations/library20-20260921.json')
    rows=sim.validate_manifest(manifest)
    assert len(rows)==20 and manifest['pool_count']==73
    summary=sim.aggregate(manifest,[])
    assert summary['unresolved']==20 and summary['passed']==0
    for change in ('url','snapshot','missing'):
        broken=copy.deepcopy(manifest)
        if change=='url':broken['samples'][0]['source_url']='https://www.bilibili.com/video/BV0000000000'
        elif change=='snapshot':broken['library_snapshot_sha256']='0'*64
        else:broken['samples'].pop()
        with pytest.raises(AssertionError):sim.validate_manifest(broken)


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


def test_observed_black_padding_is_quality_rejection_but_timeout_is_not():
    from production_diagnostics import failure_category
    reason='成片存在持续黑色填充边'
    row=dict(stage='part-quality',error_type='ValueError',reason=reason,retryable=False)
    args=dict(finals=[],validation_error='',source={},execution={},steps={})
    assert sim.classify(batch={'rejected':[row]},**args)==('rejected','candidate-quality')
    assert failure_category(reason)=='framing'
    row['retryable']=True
    assert sim.classify(batch={'rejected':[row]},**args)==('unresolved','candidate-runtime')


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
                      'git push', 'ALIBABA', 'ALICLOUD', 'actions/cache@'):
        assert forbidden not in raw
    # Simulation may retain source-bound ASR hypotheses in its own namespace.
    # It must never populate the production mother-asr cache or other state.
    saves=[s for s in workflow['jobs']['simulate']['steps']
           if s.get('uses','').startswith('actions/cache/save')]
    assert len(saves)==1
    assert saves[0]['with']=={
        'path':'linyuan/.mother_asr/${{ env.MOTHER_ASR_KEY }}',
        'key':'simulation-mother-asr-v1-${{ env.MOTHER_ASR_KEY }}'}
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
    assert set(workflow['on']['workflow_call']['inputs'])=={'sample_ids','manifest_path','content_policy','visual_chapters','text_model','title_draft_profile','recovery_run_id','output_layout','landscape_style'}
    assert workflow['on']['workflow_call']['inputs']['output_layout']['default']=='auto'
    assert workflow['on']['workflow_call']['inputs']['landscape_style']['default']=='classic'
    producer=next(s for s in simulate['steps'] if 'simulate_sources.py run --source' in s.get('run',''))
    effective={**simulate['env'],**producer.get('env',{})}
    # The real producing step used to silently override a forced landscape
    # input with OUTPUT_LAYOUT=auto, invalidating explicit-format trials.
    assert effective['OUTPUT_LAYOUT']==simulate['env']['OUTPUT_LAYOUT']
    assert effective['LINYUAN_LANDSCAPE_STYLE']==simulate['env']['LINYUAN_LANDSCAPE_STYLE']
    assert workflow['on']['workflow_call']['inputs']['visual_chapters']==dict(type='boolean',required=False,default=False)
    assert workflow['on']['workflow_call']['inputs']['text_model']['default']=='qwen3:8b'
    assert workflow['on']['workflow_call']['inputs']['title_draft_profile']['default']=='production'
    assert workflow['on']['workflow_call']['inputs']['content_policy']['default']=='legacy120'
    assert simulate['env']['LINYUAN_CONTENT_POLICY']=="${{ inputs.content_policy || 'legacy120' }}"
    assert workflow['jobs']['summary']['if']=='${{ always() && !inputs.sample_ids }}'
    assert workflow['jobs']['summary']['env']['SIMULATION_MANIFEST_PATH']==simulate['env']['SIMULATION_MANIFEST_PATH']
    assert 'inputs.manifest_path' in workflow['concurrency']['group']
    assert simulate['env']['SIMULATION_SAMPLE_IDS']=="${{ inputs.sample_ids || '' }}"


def test_diagnostic_matrix_retains_exact_fixed_source_records():
    manifest=sim.read(sim.MANIFEST)
    original=sim.validate_manifest(manifest)
    assert sim.matrix_samples(manifest)==original
    assert sim.matrix_samples(manifest,'87,5,9')==[r for r in original if r['id'] in (5,9,87)]
    for invalid in ('0','101','5,5','5,','5;echo bad','https://example.com'):
        with pytest.raises(ValueError):sim.matrix_samples(manifest,invalid)


def test_bounded_seed_acceptance_manifest_keeps_exact_urls():
    rows=[dict(id=201,slug='seed-accept-0123456789ab',
        source_url='https://www.bilibili.com/video/BVseed?p=2',
        source_preflight_sha256='a'*64)]
    manifest=dict(kind='seed_expansion_acceptance',source_preflight_run_id=123,
                  samples=rows)
    assert sim.validate_manifest(manifest)==rows
    assert sim.matrix_samples(manifest,'201')==rows


def test_publisher_probe_stays_bound_to_actual_download_and_separate_denominator():
    import copy
    manifest=sim.read(ROOT/'linyuan/simulations/publisher1-20260923.json')
    assert len(sim.validate_manifest(manifest))==1
    assert sim.aggregate(manifest,[])['total']==1
    for field,value in [('source_url','https://original.ifeng.com/c/other'),('source_preflight_sha256','a'*64)]:
        broken=copy.deepcopy(manifest);broken['samples'][0][field]=value
        with pytest.raises(AssertionError):sim.validate_manifest(broken)


def test_diagnostic_mode_cannot_emit_full_acceptance_summary(monkeypatch):
    monkeypatch.setenv('SIMULATION_SAMPLE_IDS','5,9')
    monkeypatch.setattr(sys,'argv',['simulate_sources.py','aggregate'])
    with pytest.raises(ValueError,match='diagnostic subset'):sim.main()


def test_report_retains_only_bounded_source_identity_frames(tmp_path):
    out = tmp_path / 'deliver' / 'sample'
    evidence = tmp_path / 'simulation-reports' / 'sample'
    source_tmp = out / '_tmp'
    source_tmp.mkdir(parents=True)
    for index in range(1, 8):
        (source_tmp / f'identity_{index}.jpg').write_bytes(
            f'identity-{index}'.encode())
    (source_tmp / 'identity_notes.txt').write_text('not an image')
    (source_tmp / 'identity_0.png').write_bytes(b'not a jpeg')

    assert sim.retain_identity_evidence(out, evidence) == 7
    retained = sorted((evidence / 'evidence' / '_tmp').glob('identity_*.jpg'))
    assert [path.name for path in retained] == [
        'identity_1.jpg', 'identity_2.jpg', 'identity_3.jpg',
        'identity_4.jpg', 'identity_5.jpg', 'identity_6.jpg', 'identity_7.jpg',
    ]
    assert not (evidence / 'evidence' / '_tmp' / 'identity_0.png').exists()


def test_acceptance_cannot_merge_best_results_across_diagnostics_or_revisions():
    manifest=sim.read(sim.MANIFEST)
    samples=sim.validate_manifest(manifest)
    base=[dict(sample=samples[i],status='rejected',stage='candidate-quality',finals=[],
        tested_sha='a'*40,run_id='123',publication_snapshot_sha256=manifest['comparison']['publication_sha256'])
        for i in range(2)]
    assert sim.aggregate(manifest,base)['total']==100
    for field,value in [('diagnostic_subset',True),('tested_sha','b'*40),('run_id','124'),
                        ('publication_snapshot_sha256','c'*64),('tested_sha',None)]:
        changed=[dict(base[0]),{**base[1],field:value}]
        with pytest.raises(ValueError):sim.aggregate(manifest,changed)


def test_new_library_batch_keeps_all_seventeen_eligible_sources():
    import copy
    manifest=sim.read(sim.BASE/'simulations/library17-20260923.json')
    assert len(sim.validate_manifest(manifest))==17
    for change in ('drop','swap','cutoff','snapshot'):
        broken=copy.deepcopy(manifest)
        if change=='drop':
            broken['samples'].pop();broken['denominator']=16
        elif change=='swap':broken['samples'][0]['source_url']='https://www.bilibili.com/video/BV1xx411c7mD'
        elif change=='cutoff':broken['discovery_cutoff_utc']='2026-09-20T00:00:00'
        else:broken['library_snapshot_sha256']='0'*64
        with pytest.raises(AssertionError):sim.validate_manifest(broken)
