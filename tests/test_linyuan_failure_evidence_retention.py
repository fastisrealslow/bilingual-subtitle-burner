import io
import json
from pathlib import Path
import sys
import zipfile

import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'linyuan/fc'),str(ROOT/'linyuan')]
import index as fc


@pytest.mark.parametrize('prefix,filename,report',[
    ('source-reject-','source_quality.json',{'passed':False,'retryable':False,'reason':'原始素材短边 360 < 480'}),
    ('production-reject-','batch_report.json',{'slug':'ly-negative','accepted':0,'retryable':False,
        'rejected':[{'stage':'part-quality','reason':'原始ASR存在影响理解的疑点','retryable':False}]}),
])
def test_negative_verdict_keeps_exact_artifact_and_failed_sample(monkeypatch,prefix,filename,report):
    entry=dict(slug='ly-negative',ts=1,key='exact-source',source_url='https://example.com/mother',
               source_check_attempts=2)
    state=dict(dispatched=[entry],published={},rejected=[])
    run=dict(id=10,conclusion='failure',head_sha='old')
    artifact=dict(id=9,name=prefix+'ly-negative',expired=False)
    calls=[]
    def gh(method,path,*a,**kw):
        calls.append((method,path))
        assert method=='GET','Negative evidence must never be deleted'
        return {'workflow_runs':[run]} if '/workflows/' in path else {'artifacts':[artifact]}
    data=io.BytesIO()
    with zipfile.ZipFile(data,'w') as z:z.writestr(filename,json.dumps(report))
    monkeypatch.setattr(fc,'gh',gh)
    monkeypatch.setattr(fc,'download_reviewed_zip',lambda aid,path,**kw:Path(path).write_bytes(data.getvalue()))
    monkeypatch.setattr(fc,'save_state',lambda *a:None)
    monkeypatch.setattr(fc,'log_event',lambda *a:None)
    assert fc._collect_source_rejections(state)==1
    assert state['dispatched']==[entry] and entry['failed'] and len(state['rejected'])==1
    assert entry['source_check_attempts']==2
    assert entry['failure_evidence']==dict(run_id=10,artifact_id=9,name=artifact['name'],retained=True)
    assert not any(method=='DELETE' for method,path in calls)
