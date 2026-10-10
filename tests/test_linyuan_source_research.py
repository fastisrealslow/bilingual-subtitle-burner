import json
from pathlib import Path
import random
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan'))
import source_research as research


def test_retry_is_persistent_and_success_does_not_redownload():
    job={}
    research.failed(job,TimeoutError(),1000)
    restored=json.loads(json.dumps(job))
    assert not research.due(restored,1001)
    assert research.due(restored,22600)
    restored['status']='inspected'
    assert not research.due(restored,10**10)


def test_contiguous_audio_match_is_candidate_not_original_proof():
    rng=random.Random(14)
    source=[rng.getrandbits(32) for _ in range(600)]
    ref=source[99:299]
    found=research.audio_candidate(ref,source)
    assert found and found['mother_fingerprint_offset']==99
    assert found['status']=='audio_match_candidate'
    assert found['original_publisher_confirmed'] is False
    for offset in (100,101):
        found=research.audio_candidate(source[offset:offset+200],source)
        assert found and found['mother_fingerprint_offset']==offset
    assert research.audio_candidate([rng.getrandbits(32) for _ in range(200)],source) is None
    assert research.audio_candidate([123]*200,source) is None


def test_timeout_kills_the_spawned_process_group(monkeypatch):
    import subprocess
    calls=[]
    class Process:
        pid=42
        def communicate(self,timeout=None):
            if timeout is not None:raise subprocess.TimeoutExpired('download',timeout)
            return b'',b''
    monkeypatch.setattr(research.subprocess,'Popen',lambda *a,**kw:Process())
    monkeypatch.setattr(research.os,'killpg',lambda *args:calls.append(args))
    import pytest
    with pytest.raises(subprocess.TimeoutExpired):research.bounded_run(['download'],1)
    assert calls==[(42,research.signal.SIGKILL)]


def test_cache_eviction_recovery_does_not_starve_new_sources(tmp_path):
    jobs={'old':dict(status='inspected',priority=0,evidence=dict(media_integrity='passed',
              sha256='proof',audio_fingerprint_count=80)),
          'new':dict(status='pending',priority=2)}
    ordered=research.prepare_cache_jobs(jobs,tmp_path)
    assert [key for key,_ in ordered]==['new','old']
    assert jobs['old']['evidence']['sha256']=='proof'
    assert jobs['old']['cache_recovery_needed'] and research.due(jobs['old'],1)
    folder=tmp_path/'old';folder.mkdir()
    (folder/'audio.json').write_text(json.dumps(dict(sha256='proof',values=list(range(80)))))
    research.prepare_cache_jobs(jobs,tmp_path)
    assert jobs['old']['status']=='inspected' and not jobs['old']['cache_recovery_needed']
    assert not research.due(jobs['old'],10**10)
    (folder/'audio.json').write_text(json.dumps(dict(sha256='wrong',values=list(range(80)))))
    research.prepare_cache_jobs(jobs,tmp_path)
    assert jobs['old']['cache_recovery_needed']


def test_long_mothers_precede_short_repackaging_and_commentary():
    def row(title,duration,author='访谈录制者'):
        return dict(title=title,author=author,extra=json.dumps(dict(duration=duration)))
    rows=[row('林园片段',52),row('林园访谈',1000),row('林园直播全程',2400),
          row('解说林园',5000),row('林园现场',2000,'园园滚雪球'),row('林园全文',10000)]
    assert [r['title'] for r in research.long_mother_rows(rows)]==['林园直播全程','林园访谈']


def test_visual_reclassification_overrides_stale_search_family(tmp_path,monkeypatch):
    monkeypatch.setattr(research,'BASE',tmp_path)
    (tmp_path/'up_videos.json').write_text('{}')
    url='https://m.weibo.cn/detail/5344215793927135'
    catalog=json.loads((Path(__file__).resolve().parents[1]/'linyuan/source_lineage.json').read_text())
    state=dict(jobs={research.key_for(url):dict(url=url,role='mother',family='phoenix_2026_09',priority=0)})
    research.seed_jobs(catalog,state,[])
    actual=state['jobs'][research.key_for(url)]
    assert actual['family']=='cruise_2025_09_visual' and actual['priority']==6
    assert actual['visual_classification']['production_quality']=='not_approved'
    assert any(j['url']=='https://www.yicai.com/video/103329354.html' and j['priority']==-1 for j in state['jobs'].values())
    assert state['jobs'][research.key_for('https://finance.ifeng.com/c/8wW9TEtieKg')]['priority']==-1
