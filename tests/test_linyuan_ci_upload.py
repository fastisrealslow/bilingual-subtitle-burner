"""The free route must never duplicate an uncertain submission or change gates."""
import base64
from contextlib import nullcontext
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace as NS

import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'linyuan'),str(ROOT/'linyuan/fc')]
import ci_upload as upload
import ci_upload_client as strategy
import fc_cost_policy as policy
import publish_clock
from upload_transport import RemoteUploadPending


@pytest.fixture
def case(tmp_path,monkeypatch):
    video=tmp_path/'clip.mp4';video.write_bytes(b'reviewed-video')
    cover=tmp_path/'cover.jpg';cover.write_bytes(b'approved-cover-original')
    cookies=tmp_path/'cookies.json'
    cookies.write_text(json.dumps({'cookie_info':{'cookies':[
        {'name':'DedeUserID','value':'275211725'},{'name':'bili_jct','value':'private-csrf'}]}}))
    cookies.chmod(0o600)
    cmd=['python','-m','biliup','-u',str(cookies),'upload',str(video),
        '--title','林园：真实原话','--tid','207','--copyright','3',
        '--source','公开访谈资料','--desc','当时的讨论','--tag','林园,投资',
        '--limit','1','--submit','web','--cover',str(cover)]
    events=[]
    class Response:
        def __init__(self,data):self.data=data
        def json(self):return self.data
    class Session:
        def mount(self,*a):pass
        def get(self,url,**kw):events.append('owner');return Response({'code':0,'data':{'mid':275211725}})
        def post(self,url,**kw):
            events.append('cover')
            assert url=='https://member.bilibili.com/x/vu/web/cover/up'
            assert kw['data']['cover']==b'data:image/jpeg;base64,'+base64.b64encode(cover.read_bytes())
            return Response({'code':0,'data':{'url':'https://i0.hdslb.com/approved.jpg'}})
    class Data:
        def append(self,row):events.append('append');self.video=row
    data=Data()
    class Client:
        _BiliBili__session=Session()
        def login_by_cookies(self,values):assert values['DedeUserID']=='275211725';events.append('login')
        def upload_file(self,path,lines,tasks):
            assert lines=='qn' and tasks==1 and Path(path)==video
            events.append('upload');return {'filename':'server-complete-file','title':'clip','desc':''}
        def submit_web(self):events.append('submit');return {'code':0,'data':{'bvid':'BV1Xa4j6zEVQ','aid':1}}
    client=Client()
    monkeypatch.setattr(upload,'sdk',lambda:(client,data))
    monkeypatch.setattr(upload,'deadline',lambda _:nullcontext())
    return NS(video=video,cover=cover,cookies=cookies,cmd=cmd,sha=upload.checksum(video),
              client=client,data=data,events=events)


def test_exact_publication_keeps_original_cover_title_source_and_declaration(case):
    p=upload.execute(case.cmd,case.sha,275211725)
    assert p.returncode==0 and p.stdout=='BV1Xa4j6zEVQ'
    assert p.upload_metrics['fc_invocations']==0 and p.upload_metrics['backend']=='github-qn'
    assert p.upload_metrics['video_sha256']==case.sha
    assert case.events==['login','owner','upload','cover','append','submit']
    assert (case.data.title,case.data.tid,case.data.copyright,case.data.tag)==('林园：真实原话',207,3,'林园,投资')
    assert case.data.source=='公开访谈资料' and case.data.desc=='当时的讨论'


@pytest.mark.parametrize('phase',['login','upload','cover'])
def test_failure_before_submit_is_positive_fallback_evidence(case,phase):
    def fail(*a,**kw):raise TimeoutError('lost transport response')
    if phase=='login':case.client.login_by_cookies=fail
    if phase=='upload':case.client.upload_file=fail
    if phase=='cover':case.client._BiliBili__session.post=fail
    with pytest.raises(upload.UploadNotSubmitted):upload.execute(case.cmd,case.sha,275211725)
    assert 'submit' not in case.events


def test_lost_submit_response_never_falls_back(case):
    def lost():raise TimeoutError('accepted submission response might be lost')
    case.client.submit_web=lost
    with pytest.raises(RemoteUploadPending):upload.execute(case.cmd,case.sha,275211725)


@pytest.mark.parametrize('response',[
    {'code':0,'data':{}},{'code':-500,'data':None},
    {'code':21138,'data':{'aid':1}}, {'code':21138,'data':{'bvid':'BV1Xa4j6zEVQ'}}])
def test_unknown_or_inconsistent_submission_responses_stay_pending(case,response):
    case.client.submit_web=lambda:response
    with pytest.raises(RemoteUploadPending):upload.execute(case.cmd,case.sha,275211725)


def test_only_explicit_web_unsupported_rejection_allows_fallback(case):
    case.client.submit_web=lambda:{'code':21138,'data':None}
    with pytest.raises(upload.UploadNotSubmitted,match='explicitly_unsupported'):
        upload.execute(case.cmd,case.sha,275211725)


@pytest.mark.parametrize('tamper',['video','cookie_owner','authenticated_owner','options'])
def test_unreviewed_bytes_wrong_account_or_policy_never_submit(case,tamper):
    if tamper=='video':case.video.write_bytes(b'wrong-video')
    if tamper=='cookie_owner':case.cookies.write_text('{"cookies":[]}')
    if tamper=='authenticated_owner':case.client._BiliBili__session.get=lambda *a,**kw:NS(json=lambda:{'code':0,'data':{'mid':99}})
    if tamper=='options':case.cmd+=['--dtime','2000000000']
    with pytest.raises(upload.UploadNotSubmitted):upload.execute(case.cmd,case.sha,275211725)
    assert 'submit' not in case.events


def test_file_changed_during_upload_stops_before_submit(case):
    def changed(*a,**kw):case.video.write_bytes(b'tampered');return {'filename':'file'}
    case.client.upload_file=changed
    with pytest.raises(upload.UploadNotSubmitted):upload.execute(case.cmd,case.sha,275211725)
    assert 'submit' not in case.events


@pytest.fixture
def fallback(monkeypatch):
    calls=[];entry={};state={'dispatched':[entry]};part={'fingerprints':{'sha256':'a'*64}}
    fc=NS(OWNER_MID=275211725,save_state=lambda st:calls.append('save') or True)
    def primary(*a):raise upload.UploadNotSubmitted('network_before_submit')
    monkeypatch.setattr(strategy,'upload_on_ci',primary)
    monkeypatch.setattr(strategy.domestic,'capability',lambda:calls.append('health') or True)
    def prepare(*a):
        calls.append('prepare');binding={'task_id':'known-id'};entry['upload_transport']=binding;return binding
    monkeypatch.setattr(strategy.domestic,'prepare',prepare)
    monkeypatch.setattr(strategy.domestic,'execute',lambda b:calls.append('fc-upload') or subprocess.CompletedProcess([],0,'BV1Xa4j6zEVQ',''))
    return fc,state,entry,part,calls


def test_primary_success_never_prepares_or_invokes_fc(monkeypatch,fallback):
    fc,state,entry,part,calls=fallback
    monkeypatch.setattr(strategy,'upload_on_ci',lambda *a:subprocess.CompletedProcess([],0,'BV1Xa4j6zEVQ',''))
    assert strategy.execute(fc,state,entry,part,[],'.').returncode==0
    assert calls==[]


def test_fallback_is_bound_and_persisted_before_the_only_domestic_invocation(fallback):
    fc,state,entry,part,calls=fallback
    assert strategy.execute(fc,state,entry,part,[],'.').returncode==0
    assert calls==['health','prepare','save','fc-upload']
    assert entry['upload_transport']['video_sha256']=='a'*64


def test_primary_unknown_never_probes_or_reuploads_on_fc(monkeypatch,fallback):
    fc,state,entry,part,calls=fallback
    def unknown(*a):raise RemoteUploadPending('unknown submit')
    monkeypatch.setattr(strategy,'upload_on_ci',unknown)
    with pytest.raises(RemoteUploadPending):strategy.execute(fc,state,entry,part,[],'.')
    assert calls==[]


def test_unavailable_fallback_is_definitely_not_invoked(monkeypatch,fallback):
    fc,state,entry,part,calls=fallback
    monkeypatch.setattr(strategy.domestic,'capability',lambda:False)
    with pytest.raises(strategy.UploadDefinitelyNotStarted):strategy.execute(fc,state,entry,part,[],'.')
    assert calls==[]


def test_unknown_remote_fallback_does_not_release_its_intent(monkeypatch,fallback):
    fc,state,entry,part,calls=fallback
    def unknown(*a):raise RemoteUploadPending('unknown FC task')
    monkeypatch.setattr(strategy.domestic,'execute',unknown)
    with pytest.raises(RemoteUploadPending):strategy.execute(fc,state,entry,part,[],'.')
    assert entry['upload_transport']['task_id']=='known-id' and calls[-1]=='save'


def test_empty_daily_recovery_makes_only_one_state_read():
    calls=[]
    fc=NS(load_state=lambda:calls.append('state') or {},catchup_deficit=lambda _:0)
    assert publish_clock.recover(fc)['published']==0 and calls==['state']


def test_recovery_uses_old_slot_gates_caps_four_and_does_not_wait_for_busy_lease(monkeypatch):
    calls=[]
    def lease(kind,action,wait_seconds):
        assert kind=='publish' and wait_seconds==0;return action()
    fc=NS(load_state=lambda:{},catchup_deficit=lambda _:1,run_with_lease=lease,
          publish_catchup=lambda event:calls.append(event) or {'published':1},flush_logs=lambda:None)
    monkeypatch.setenv('LINYUAN_PUBLISH_BACKEND','ci-preferred')
    assert publish_clock.recover(fc)['published']==4 and len(calls)==4
    import os
    assert os.environ['LINYUAN_PUBLISH_BACKEND']=='ci-preferred'


def test_clock_cutover_keeps_legacy_until_both_acceptance_flags_are_enabled(monkeypatch):
    monkeypatch.setattr(policy,'RUNNER_PUBLISH_ENABLED',True)
    monkeypatch.setattr(policy,'CI_CLOCK_ENABLED',False)
    assert policy.publish_timer()['cron']=='0 0 2,6,8,13 * * *'
    monkeypatch.setattr(policy,'CI_CLOCK_ENABLED',True)
    t=policy.publish_timer()
    assert t['cron']=='0 55 13 * * *' and t['payload']==dict(triggerName='publish-recovery',purpose='last_chance_daily_four')


def test_clock_cutover_rejects_a_fabricated_or_wrong_file_receipt(tmp_path):
    import rollout_acceptance as accept
    with pytest.raises(SystemExit,match='durable'):accept.assert_clock_acceptance(tmp_path)
    for name in accept.FILES:
        path=tmp_path/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('same reviewed code')
    sha='b'*64
    proof=dict(backend='github-qn',public=True,owner_verified=True,fc_invocations=0,
        bvid='BV1Xa4j6zEVQ',sha256=sha,experiment_compliant=True,code_hashes=accept.code_hashes(tmp_path))
    (tmp_path/accept.PROOF).parent.mkdir(parents=True,exist_ok=True)
    (tmp_path/accept.PROOF).write_text(json.dumps(proof))
    state={'published':{'test':{'parts':[]}}}
    path=tmp_path/'linyuan/.automation/fc_state.json';path.write_text(json.dumps(state))
    with pytest.raises(SystemExit,match='exact durable'):accept.assert_clock_acceptance(tmp_path)
    state['published']['test']['parts']=[dict(status='published',bvid=proof['bvid'],fingerprints={'sha256':sha},upload_backend='github-qn',publishing_experiment={'compliant':True})]
    path.write_text(json.dumps(state))
    assert accept.assert_clock_acceptance(tmp_path)==proof
    (tmp_path/accept.FILES[0]).write_text('different untested uploader')
    with pytest.raises(SystemExit,match='different code'):accept.assert_clock_acceptance(tmp_path)


def test_future_canary_cannot_publish_before_an_arbitrary_time(monkeypatch):
    import publish_on_runner as runner
    import time
    with pytest.raises(ValueError):runner.run(not_before=time.time()+10000)
    with pytest.raises(ValueError):runner.run(not_before='tomorrow')


def test_upload_compatibility_tolerates_only_activation_flags(monkeypatch):
    import reviewed_upload_client as client
    import index as fc
    monkeypatch.setattr(policy,'RUNNER_PUBLISH_ENABLED',True)
    monkeypatch.setattr(policy,'CI_CLOCK_ENABLED',True)
    health=fc.production_config()
    # General test fixtures select legacy120; deployed runtime uses reference_v1.
    health.update(content_policy='reference_v1',minimum_final_seconds=20)
    old=deepcopy(health)
    old['fc_cost_policy'].update(runner_publish_enabled=False,policy_sha256='0'*64,
        publish_timer=dict(cron=policy.LEGACY_PUBLISH_CRON,payload={'triggerName':'publish'},recovery=False))
    assert client.compatible_upload_health(old)
    for field in ['code_sha256','editorial_code_sha256','caption_readability_sha256']:
        bad=deepcopy(old);bad[field]='0'*64
        assert not client.compatible_upload_health(bad)
    bad=deepcopy(old);bad['fc_cost_policy']['upload_transport_sha256']='0'*64
    assert not client.compatible_upload_health(bad)
    bad=deepcopy(old);bad['fc_cost_policy']['publish_timer']['cron']='* * * * * *'
    assert not client.compatible_upload_health(bad)


def test_recovery_timer_never_cuts_over_without_real_acceptance(monkeypatch):
    import verify_production as verify
    import rollout_acceptance as acceptance
    monkeypatch.setattr(policy,'RUNNER_PUBLISH_ENABLED',True)
    monkeypatch.setattr(policy,'CI_CLOCK_ENABLED',True)
    triggers=[NS(trigger_name=n,trigger_type='timer',qualifier='LATEST',
                 trigger_config=json.dumps(dict(cronExpression=cron,enable=True,payload=json.dumps({'triggerName':n}))))
              for n,cron in [('publish',policy.LEGACY_PUBLISH_CRON),('dispatch','0 37 * * * *')]]
    calls=[]
    class Client:
        def list_triggers_with_options(self,*a):return NS(body=NS(triggers=triggers))
        def update_trigger_with_options(self,*a):calls.append(a)
    def unaccepted(*a):raise SystemExit('missing real acceptance')
    monkeypatch.setattr(acceptance,'assert_clock_acceptance',unaccepted)
    with pytest.raises(SystemExit,match='real acceptance'):
        verify.configure_timers(Client(),'fc',NS(ListTriggersRequest=NS),NS())
    assert calls==[]


def test_verified_recovery_clock_preserves_source_retirement_and_daily_limit(monkeypatch):
    import verify_production as verify
    import rollout_acceptance as acceptance
    import dispatch_on_runner as dispatch
    import index as fc
    monkeypatch.setattr(policy,'RUNNER_PUBLISH_ENABLED',True)
    monkeypatch.setattr(policy,'CI_CLOCK_ENABLED',True)
    monkeypatch.setattr(acceptance,'assert_clock_acceptance',lambda:{'public':True})
    triggers={n:NS(trigger_name=n,trigger_type='timer',qualifier='LATEST',
        trigger_config=json.dumps(dict(cronExpression=cron,enable=(n=='publish'),payload=json.dumps({'triggerName':n}))))
        for n,cron in [('publish',policy.LEGACY_PUBLISH_CRON),('dispatch','0 37 * * * *')]}
    class Client:
        def list_triggers_with_options(self,*a):return NS(body=NS(triggers=list(triggers.values())))
        def update_trigger_with_options(self,function,name,req,*a):
            triggers[name].trigger_config=req.body.trigger_config
            triggers[name].qualifier=getattr(req.body,'qualifier',None) or 'LATEST'
    models=NS(ListTriggersRequest=NS,UpdateTriggerRequest=NS,UpdateTriggerInput=NS)
    verify.configure_timers(Client(),'fc',models,NS())
    cfg=json.loads(triggers['publish'].trigger_config)
    assert cfg['enable'] is True and cfg['cronExpression']==policy.RECOVERY_CRON
    assert json.loads(cfg['payload'])==policy.publish_timer()['payload']
    proof=dispatch.retire_hourly_timer(Client(),'fc',models,NS())
    assert proof['publish_enabled'] and not proof['dispatch_enabled']
    assert fc.MAX_PUBLISH_PER_DAY==4 and sorted(fc.PUBLISH_HOURS)==[10,14,16,21]


@pytest.mark.parametrize('bad',['public','slot','duration','backend','experiment'])
def test_live_canary_proof_requires_the_actual_public_same_experiment_and_slot(tmp_path,bad):
    import rollout_acceptance as accept
    for name in accept.FILES:
        p=tmp_path/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('reviewed code')
    bvid='BV1Xa4j6zEVQ';sha='a'*64;slot='2026-11-10 14'
    part=dict(status='published',bvid=bvid,title='林园：真实原话',duration_sec=80,ts=1000,
        scheduled_slot=slot,fingerprints={'sha256':sha},upload_backend='github-qn',
        upload_metrics=dict(fc_invocations=0,video_sha256=sha,total_seconds=5),
        publishing_experiment={'compliant':True})
    state={'published':{'clip':{'parts':[part]}}}
    video=dict(bvid=bvid,public=True,mid=275211725,title=part['title'],duration=80,ctime=1000)
    creator=dict(owner_verified=True,owner_mid=275211725,videos=[video])
    assert accept.proof_for_publication(state,bvid,creator,slot,tmp_path)['sha256']==sha
    if bad=='public':video['public']=False
    if bad=='slot':part['scheduled_slot']='2026-11-10 10'
    if bad=='duration':video['duration']=50
    if bad=='backend':part['upload_backend']='fc-reviewed'
    if bad=='experiment':part['publishing_experiment']['compliant']=False
    with pytest.raises(ValueError):accept.proof_for_publication(state,bvid,creator,slot,tmp_path)


def test_hard_deadline_escapes_the_sdk_timeout_retry_handler():
    import time
    with pytest.raises(upload.UploadDeadlineExpired):
        with upload.deadline(.02):
            try:time.sleep(.2)
            except TimeoutError:pytest.fail('SDK would retry and swallow this deadline')
