"""An absent optimization must not prevent normal raw-source verification."""
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan'))
import restore_production_evidence as R


@pytest.mark.parametrize('rows',[[],[dict(name='editorial-old',expired=True)],
                                  [dict(name='deliver-old',expired=False)]])
def test_expired_or_absent_raw_cache_uses_normal_fresh_asr(monkeypatch,tmp_path,rows):
    monkeypatch.setattr(sys,'argv',['restore','--run-id','123','--work',str(tmp_path)])
    monkeypatch.setattr(R.subprocess,'check_output',lambda *a,**k:json.dumps(dict(artifacts=rows)).encode())
    monkeypatch.setattr(R.subprocess,'run',lambda *a,**k:pytest.fail('must not download nonexistent evidence'))
    monkeypatch.setattr(R,'restore',lambda *a,**k:pytest.fail('must not fabricate evidence'))
    assert R.main() is None and not list(tmp_path.iterdir())
