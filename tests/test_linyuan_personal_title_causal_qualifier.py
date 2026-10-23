"""A real generated headline lost an explicit causal qualifier."""
import json
from pathlib import Path
import sys
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan'))
import title_rewrite as T


def test_actual_generated_title_and_cover_each_preserve_personal_cause_scope():
    f=json.loads((Path(__file__).parent/'fixtures/linyuan_actual_personal_cause_qualifier.json').read_text())
    bad=f['rejected_title'];source=f['transcript']
    assert T.forecast_copy_error(bad,'我只是有点阿Q精神',source)
    assert T.forecast_copy_error('林园：我遇事比较乐观',bad,source)
    assert T.personal_cause_qualifier_error('林园：我遇事比较乐观，可能与基因有关','我遇事比较乐观',source) is None
    assert T.personal_cause_qualifier_error('林园：股市大跌是常事，我骨子里多点阿Q精神','多点阿Q精神',source) is None
    # This narrow negative rule cannot invent uncertainty in another source.
    assert T.personal_cause_qualifier_error(bad,bad,'一个人的乐观由基因决定。') is None
