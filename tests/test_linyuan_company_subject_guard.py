"""Run39883678616: 谢谢/林总 must not become forced company title objects."""
from pathlib import Path
import sys

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan'))
import publishing_experiment as E
import produce_cn as P


@pytest.mark.parametrize('text',[
    '好，谢谢林总啊，我们再来看这位网友想问一下啊，银行板块的估值很低，现在可以抄底吗？',
    '谢谢王老师，医药行业未来的机会在哪里？',
    '感谢张博士，网友想请教银行的估值和盈利。',
    '欢迎陈先生，您怎么看中国金融行业的行情？',
    '各位朋友，指数基金的成份股调仓机制是什么？',
])
def test_greetings_addresses_and_sector_labels_are_not_named_company_objects(text):
    selection=dict(kind='company_case',subject_quote=text,event_quote='我们当年退出了。',detail_quote='因为看不清楚其中的风险。')
    assert not E.title_focus(selection)['subjects']
    assert E.company_subject_error(selection)


@pytest.mark.parametrize('company',['茅台','腾讯','招商银行','可口可乐','NVIDIA'])
def test_real_named_company_survives_polite_leadin(company):
    s=dict(kind='company_case',subject_quote='谢谢林总，网友想请教您当年为什么投资'+company+'？',
           event_quote='我们当时已经作出了这个投资选择。',detail_quote='因为看过它的产品。')
    subjects=E.title_focus(s)['subjects']
    assert company in subjects and '谢谢' not in subjects and '林总' not in subjects
    assert E.company_subject_error(s) is None


def test_model_must_make_fresh_personal_choice_not_silently_relabel_bad_company():
    subject='谢谢林总，银行板块估值很低，现在可以抄底吗？'
    event='我们一六年四月份已经退出银行板块了。'
    detail='看不清楚的事，命运掌握不到我手上的事，我就不去参与了。'
    cues=[dict(text=subject+event+detail)];picks=[dict(start=0,end=0)]
    choice=dict(index=0,kind='company_case',subject_quote=subject,event_quote=event,detail_quote=detail)
    with pytest.raises(ValueError,match='Named company'):
        E.validate_choices(cues,picks,dict(choices=[choice]))
    # A test-only explicit independent verdict may use the existing personal
    # choice route. The production guard NEVER changes kind on its own.
    chosen=E.validate_choices(cues,picks,dict(choices=[{**choice,'kind':'personal_story'}]))
    assert chosen[0]['story_selection']['kind']=='personal_story'


def test_invalid_cached_company_story_stops_before_picture_title_or_audit(tmp_path,monkeypatch):
    monkeypatch.setenv('LINYUAN_EDITORIAL_PROFILE',E.TREATMENT)
    monkeypatch.setattr(P,'argument_record_for_render',lambda *a:pytest.fail('budget consumed on bad company subject'))
    pick=dict(start=0,end=0,story_selection=dict(kind='company_case',
        subject_quote='谢谢林总，银行板块的估值低吗？',event_quote='我们已经退出了。',detail_quote='因为看不清风险。'))
    with pytest.raises(P.VisualQualityError,match='故事选题证据无效'):
        P._produce_one(Path('unused.mp4'),tmp_path,tmp_path,[dict(text='原文不改。')],
            '林园','访谈','',False,1920,1080,'',preselected_picks=[pick])


def test_existing_fixture_story_proof_remains_valid_and_definitions_never_reset():
    import test_linyuan_publishing_experiment as fixtures
    meta,cues,_=fixtures.treatment()
    assert E.proof_error(meta) is None
    assert E.VERSION==2026101801 and E.SELECTION_REVISION==2026102201
    assert E.EXPERIMENT_ID=='field-story-30d-20261018' and E.MIN_MOTHER_SECONDS==1200
