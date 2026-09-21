"""Reference formats keep source completeness and distinguish legacy stock."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'linyuan'))
import editorial_policy as policy


def metadata(seconds=55):
    return dict(duration_sec=seconds, segments=[dict(start=10,end=10+seconds)],
        content_policy='reference_v1',content_format=policy.content_format(seconds),
        duration_policy_version=policy.DURATION_POLICY_VERSION,
        editorial_review=dict(version=policy.VERSION,standalone_opening=True,
            complete_argument=True,reasoning_present=True,natural_ending=True,
            requires_audio_review=False,summary='一个完整问答',transcript_sha256='abc'))


@pytest.mark.parametrize('seconds,category', [(27,'short_view'),(99,'short_view'),
    (119.6,'short_view'),(120,'complete_view'),(253,'complete_view'),(680,'long_interview')])
def test_reference_lengths_are_explicit_and_source_continuous(seconds,category):
    meta=metadata(seconds)
    assert meta['content_format']==category
    assert policy.metadata_error(meta,seconds) is None


def test_short_policy_cannot_approve_legacy_or_padded_or_unreviewed_clip():
    legacy=metadata();legacy.pop('content_policy')
    assert '120秒' in policy.metadata_error(legacy,55)
    assert policy.metadata_error(metadata(19.9),19.9)
    assert policy.metadata_error(metadata(),18)
    changed=metadata();changed['segments'][0]['end']-=8
    assert policy.metadata_error(changed,55)
    changed=metadata();changed['editorial_review']['natural_ending']=False
    assert policy.metadata_error(changed,55)
    changed=metadata();changed['content_format']='complete_view'
    assert policy.metadata_error(changed,55)
    changed=metadata();changed['duration_policy_version']=0
    assert policy.metadata_error(changed,55)


def test_production_defaults_to_reference_policy_and_invalid_policy_fails():
    env={k:v for k,v in os.environ.items() if k!='LINYUAN_CONTENT_POLICY'}
    script="import sys;sys.path.insert(0,'linyuan');import editorial_policy as e;assert e.CONTENT_POLICY=='reference_v1' and e.MIN_SECONDS==20"
    subprocess.run([sys.executable,'-c',script],cwd=ROOT,env=env,check=True,capture_output=True)
    bad=subprocess.run([sys.executable,'-c',script],cwd=ROOT,
        env={**env,'LINYUAN_CONTENT_POLICY':'accept_anything'},capture_output=True)
    assert bad.returncode!=0 and b'Unknown LINYUAN_CONTENT_POLICY' in bad.stderr


def test_real_source6_short_turns_are_not_stitched_to_reach_120():
    script='''
import json,sys
sys.path.insert(0,'linyuan')
import editorial_policy as e, source_selection as s, produce_cn as p
sys.path.insert(0,'linyuan/fc');import index as fc
c=json.load(open('tests/fixtures/linyuan_source6_short_turns.json'))['cues']
picks=s.select(c,limit=None,whole_source=True)
print(json.dumps(dict(policy=e.CONTENT_POLICY,minimum=e.MIN_SECONDS,source_minimum=p.SOURCE_MIN_DURATION,
 dispatch_minimum=fc.MIN_DUR,picks=[dict(start=c[x['start']]['start'],end=c[x['end']]['end'],
 duration=e.range_seconds(c,x)) for x in picks],identity=e.plan_identity(c,180))))
'''
    results={}
    for name in ('legacy120','reference_v1'):
        env={**os.environ,'LINYUAN_CONTENT_POLICY':name}
        run=subprocess.run([sys.executable,'-c',script],cwd=ROOT,env=env,check=True,
                           text=True,capture_output=True,timeout=60)
        results[name]=json.loads(run.stdout.strip().splitlines()[-1])
    old,new=results['legacy120'],results['reference_v1']
    assert old['picks']==[]
    assert new['minimum']==new['source_minimum']==new['dispatch_minimum']==20
    assert len(new['picks'])==6
    assert all(20<=p['duration']<120 for p in new['picks'])
    assert all(a['end']<=b['start'] for a,b in zip(new['picks'],new['picks'][1:]))
    assert old['identity']!=new['identity']


def test_real_short_openings_keep_speech_bytes_and_do_not_invent_context(monkeypatch):
    import source_selection as selection
    monkeypatch.setattr(policy,'CONTENT_POLICY','reference_v1')
    monkeypatch.setattr(policy,'MIN_SECONDS',20)
    corpus=json.loads((ROOT/'tests/fixtures/linyuan_short_source_openings.json').read_text())
    observed={}
    for row in corpus:
        cues=row['cues'];before=json.dumps(cues,ensure_ascii=False)
        picks=selection.select(cues,whole_source=True,limit=None)
        observed[row['id']]=[(cues[p['start']]['start'],cues[p['end']]['end']) for p in picks]
        assert json.dumps(cues,ensure_ascii=False)==before
        assert selection.select(cues,whole_source=False,limit=None)==[]
    assert observed=={2:[(15.36,52.28)],14:[(0.16,65.96)],55:[]}
    assert not selection.speech_opening('可能我的想法跟你平时的感受啊，和你们的感受还是不一样，太紧张啊，这些焦虑没有。')
    assert not selection.speech_opening('这个啊，是便宜的。')
