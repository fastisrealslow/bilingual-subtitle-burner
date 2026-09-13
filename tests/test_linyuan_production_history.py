import base64
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan'))
import publication_history as history
import production_failure_report as failure
import produce_cn as producer


def test_large_history_retains_receipts_via_exact_blob():
    state={'published':{'done':{'bvid':'BVdone'}},'dispatched':[], 'history':'x'*(1024*1024)}
    calls=[]
    def api(path):
        calls.append(path)
        if '/contents/' in path:return {'sha':'known','content':'','encoding':'none'}
        return {'sha':'known','encoding':'base64',
                'content':base64.b64encode(json.dumps(state).encode()).decode()}
    assert history.fetch('owner/repo',api)==state
    assert calls[-1].endswith('/git/blobs/known')


def test_empty_history_stops_before_asr(monkeypatch,tmp_path):
    state=tmp_path/'state.json';state.write_text('')
    monkeypatch.setenv('PUBLICATION_STATE_PATH',str(state))
    monkeypatch.setattr(sys,'argv',['produce','--source','nonexistent.mp4','--slug','test'])
    monkeypatch.setattr(producer,'transcribe',lambda *a:pytest.fail('ASR ran with missing history'))
    with pytest.raises(json.JSONDecodeError):producer.main()


def test_unhandled_failure_produces_recoverable_report_without_overwriting_verdict(tmp_path):
    path=tmp_path/'batch_report.json'
    assert failure.report_missing(path,{'render':{'outcome':'failure'}},'mother')
    report=json.loads(path.read_text())
    assert report['retryable'] and not report['selection_completed']
    path.write_text('{"accepted":1,"accepted_finals":["final.mp4"]}')
    assert not failure.report_missing(path,{'render':{'outcome':'failure'}},'mother')
    assert json.loads(path.read_text())['accepted']==1


def test_source_rejection_is_not_relabelled_as_runtime_failure(tmp_path):
    path=tmp_path/'batch_report.json'
    assert not failure.report_missing(path,{'source_gate':{'outcome':'failure'}},'mother')
    assert not path.exists()


def test_full_interview_cpu_prefill_is_not_capped_at_short_title_budget(monkeypatch):
    monkeypatch.setattr(producer,'TEXT_BACKEND','local')
    # Run 794 made three identical 240-second failures on 11,385 source chars.
    assert producer.title_inference_budget('文'*11385,full=True)>600
    assert producer.title_inference_budget('文'*11385,full=False)<=600
    assert producer.title_inference_budget('文'*100000,full=True)<=1800


def test_title_timeout_does_not_start_three_immediate_full_transcript_requests():
    import title_rewrite
    calls=[]
    def unavailable(*args):
        calls.append(args)
        raise producer.LocalTextUnavailable('CPU timed out')
    transcript=('您对医药股有什么判断？医药行业需求随着老龄化增长，我们长期持有这些企业。'
                '但是投资仍然有风险，价格和需求都要看，不能只看过去。'
                '企业产品有需求，投资才有长期增长的基础。')
    result=title_rewrite.generate(transcript,structured_model=unavailable)
    assert result['title_rewrite']['review']['method']=='source_quote'
    assert len(calls)==1
    calls.clear()
    with pytest.raises(producer.LocalTextUnavailable):
        title_rewrite.generate('残句',structured_model=unavailable)
    assert len(calls)==1
