from copy import deepcopy
import json
from pathlib import Path
import sys
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import linyuan_content_stages as S


def test_brief_cannot_bind_example_as_core_or_invent_subject():
    units=['您买石油了吗？','我没买，石油可能有替代品。','比如红烧肉一直有人吃。']
    brief=dict(a_question='是否买石油',b_main_answer='没买，因为可能有替代品',c_subject='石油',
        d_core_ids=[1],e_example_ids=[2],f_qualifier_ids=[1],g_missing_context='无')
    assert S.validate_brief(brief,units)==brief
    for change in ({'d_core_ids':[2]}, {'c_subject':'茅台'}, {'d_core_ids':[True]}, {'f_qualifier_ids':[9]}):
        with pytest.raises(ValueError):S.validate_brief({**brief,**change},units)


def test_artifact_transfer_rejects_changed_source_code_stage_and_profile():
    case=json.loads(S.CORPUS.read_text())[0];expected=S.binding(case)
    row=dict(binding=expected,stage='read',profile='8b-reader',status='completed')
    S.validate_input(row,expected,'read','8b-reader')
    for field in ('source_sha256','transcript_sha256','cues_sha256','commit','run_id','code_sha256'):
        bad=deepcopy(row);bad['binding'][field]='changed'
        with pytest.raises(ValueError):S.validate_input(bad,expected,'read','8b-reader')
    for patch in ({'stage':'review'},{'profile':'9b-reader'},{'status':'unresolved'}):
        with pytest.raises(ValueError):S.validate_input({**row,**patch},expected,'read','8b-reader')


def test_blind_critic_never_receives_writer_evidence_or_reader_rationale(tmp_path,monkeypatch):
    case=json.loads(S.CORPUS.read_text())[0]
    prior=dict(binding=S.binding(case),stage='write',profile='8b-reader',status='completed',
        result={'candidates':[dict(title='林园：我没买中石油，石油可能有替代品',cover_title='石油可能被替代，我没买',
            evidence_ids=[1],private_writer_rationale='DONT_SEND_RATIONALE') for _ in range(3)]})
    path=tmp_path/'write.json';path.write_text(json.dumps(prior))
    def ask(prompt,schema,model,row,save):
        assert 'DONT_SEND_RATIONALE' not in prompt
        assert 'manual_control' not in prompt and 'generated-' not in prompt
        assert '没买' in prompt
        shown=json.loads(prompt.split('待审文案：')[1].split('\n')[0])
        assert isinstance(shown,dict) and set(shown)=={'id','title','cover_title'}
        assert prompt.count('待审文案：')==1
        clauses=S.copy_clauses(shown)
        return dict(a_analysis=dict(reason='仅用于测试结构交接',source_ids=[1]),b_verdict=dict(id=shown['id'],
            **{k:False for k in ('faithful','main_answer','speaker_correct','qualifiers_kept','standalone','natural')}),
            a0_clause_checks=[dict(a_id=c['id'],b_reason='测试证据',c_source_ids=[1],d_supported=False) for c in clauses])
    monkeypatch.setattr(S,'ask',ask)
    monkeypatch.setattr(sys,'argv',['experiment','review','--case','0','--profile','8b-reader','--input',str(path),'--out',str(tmp_path)])
    S.main();result=json.loads((tmp_path/'review.json').read_text())
    assert len(result['result']['reviews'])==5 and result['automatic_critic_passes']==[]
    assert result['editorial_approved'] is False and result['production_authorized'] is False


def test_malformed_sibling_does_not_discard_bound_draft():
    good=dict(title='中石油不符合我的标准',cover_title='中石油不符合我的标准',evidence_ids=[1])
    accepted,rejected=S.partition_drafts([good,{**good,'evidence_ids':[2]},{**good,'evidence_ids':[True]}],
        {'d_core_ids':[1]},['主持人提问','我没买中石油','旁枝例子'])
    assert len(accepted)==1 and accepted[0]['draft_index']==0
    assert [r['index'] for r in rejected]==[1,2]


def test_critic_must_cover_second_clause_and_cover_not_just_first_claim():
    clauses=S.copy_clauses(dict(title='林园：市场空间很大，但药物效果不确定',cover_title='药物效果仍待验证'))
    assert [c['text'] for c in clauses]==['市场空间很大','但药物效果不确定','药物效果仍待验证']
    checks=[dict(a_id=i,b_reason='逐句比较',c_source_ids=[0],d_supported=True) for i in range(3)]
    assert S.validate_clause_checks(checks,clauses,['原文'])
    for bad in (checks[:1],checks[:2]+[checks[0]],checks[:2]+[{**checks[2],'c_source_ids':[]}]):
        with pytest.raises(ValueError):S.validate_clause_checks(bad,clauses,['原文'])
    assert not S.validate_clause_checks(checks[:2]+[{**checks[2],'d_supported':False}],clauses,['原文'])
