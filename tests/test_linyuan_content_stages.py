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
        return {'reviews':[dict(a_analysis=dict(reason='仅用于测试结构交接',source_ids=[1]),b_verdict=dict(id=i,
            **{k:False for k in ('faithful','main_answer','speaker_correct','qualifiers_kept','standalone','natural')})) for i in range(5)]}
    monkeypatch.setattr(S,'ask',ask)
    monkeypatch.setattr(sys,'argv',['experiment','review','--case','0','--profile','8b-reader','--input',str(path),'--out',str(tmp_path)])
    S.main();result=json.loads((tmp_path/'review.json').read_text())
    assert len(result['result']['reviews'])==5 and result['automatic_critic_passes']==[]
    assert result['editorial_approved'] is False and result['production_authorized'] is False
