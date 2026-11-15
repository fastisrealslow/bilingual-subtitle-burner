import json
from pathlib import Path
import sys
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan'))
import title_rewrite as T


def test_actual_published_proof_is_rechecked():
    data=json.loads((Path(__file__).parent/'fixtures/linyuan_research_progress_claim.json').read_text())
    assert all(data['proof']['review'][k] for k in T.CHECKS)
    assert '研究进展' in T.error(data['title'],data['proof'],data['transcript'])


@pytest.mark.parametrize('source,bad',[
 ('健康和消费都值得研究。但是我们真正的研究清楚的东西，那只有消费。','唯有消费可研究'),
 ('科技和医药都值得研究。我们研究明白的只有医药。','只有医药值得研究'),
 ('我们调研透彻的公司仅有银行。','仅有银行可以研究'),
 ('我们研究透的只有食品。','唯有食品能研究'),
])
def test_same_predicate_change_in_either_surface(source,bad):
    for title,cover in [(bad,'研究需要耐心'),('研究需要耐心',bad)]:
        assert '研究进展' in T.research_scope_error(title,cover,source)


@pytest.mark.parametrize('source,copy',[
 ('我们研究清楚的只有消费。','我们研究清楚的只有消费'),
 ('我们研究明白的只有医药。','我们只研究明白了医药'),
 ('只有医药值得研究。','只有医药值得研究'),
 ('我们研究清楚的只有消费。银行值得研究。','只有银行值得研究'),
 ('我们研究清楚的只有消费。只有消费值得研究。','只有消费值得研究'),
 ('我们研究清楚的只有消费。','健康和消费都值得研究'),
])
def test_normal_counterexamples(source,copy):
    assert T.research_scope_error(copy,copy,source) is None


@pytest.mark.parametrize('extra',[
 '难道只有医药值得研究？',
 '并非只有医药值得研究。',
 '是不是只有医药值得研究？',
])
def test_question_or_negation_does_not_authorize_change(extra):
    source='我们研究清楚的只有医药。'+extra
    assert T.research_scope_error('只有医药值得研究','只有医药值得研究',source)
