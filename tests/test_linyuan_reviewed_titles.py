"""Actual inspected title results must survive default production without drift."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'linyuan'))
import reviewed_title_records as R
import produce_cn as P
import title_rewrite as T

CASES=[r for r in json.loads((ROOT/'linyuan/simulations/benchmark-20260921/all-current-title-corpus.json').read_text())
       if r['source_id'] in (13,28)]


@pytest.mark.parametrize('case',CASES,ids=lambda r:str(r['source_id']))
def test_actual_inspected_copy_is_kept_by_default_and_retains_old_provenance(case,tmp_path,monkeypatch):
    monkeypatch.delenv('LINYUAN_TITLE_HANDOFF',raising=False)
    monkeypatch.setattr(T,'generate',lambda *a,**kw:pytest.fail('Known inspected title must not drift'))
    cues=case['cues'];source=case['source_sha256'];text=''.join(c['text'] for c in cues)
    expected,proof=R.lookup(source,text,'林园')
    output=P.copywrite(cues,list(range(len(cues))),'林园','',None,tmp_path,source_sha256=source)
    assert output['title']==expected['title'] and output['cover_title']==expected['cover_title']
    assert output['reviewed_title_record']['provenance']==proof['provenance']
    assert output['reviewed_title_record']['editorial_approved'] is False
    assert output['copy_identity']['title_editor_sha256']==P._sha256_file(Path(T.__file__))
    assert T.error(output['title'],output['title_rewrite'],transcript=text) is None
    assert output['desc'] and output['tags'] and output['title_candidates']
    assert R.lookup('f'*64,text,'林园') is None
    assert R.lookup(source,text+'这是不同的完整观点。','林园') is None
    assert R.lookup(source,text,'其他人') is None


def test_changed_copy_or_current_gate_cannot_be_bypassed(tmp_path,monkeypatch):
    case=CASES[0];text=''.join(c['text'] for c in case['cues'])
    records=json.loads((ROOT/'linyuan/reviewed_title_records.json').read_text())
    bad=deepcopy(records);bad[0]['result']['cover_title']='中石油保证赚钱'
    path=tmp_path/'records.json';path.write_text(json.dumps(bad))
    with pytest.raises(ValueError,match='封面文案'):
        R.lookup(case['source_sha256'],text,'林园',path)
    monkeypatch.setattr(T,'error',lambda *a,**kw:'new policy rejects this claim')
    with pytest.raises(ValueError,match='当前证据检查'):
        R.lookup(case['source_sha256'],text,'林园')
