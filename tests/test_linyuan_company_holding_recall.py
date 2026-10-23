from copy import deepcopy
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan'))
import publishing_experiment as E


def holding():
    return [dict(text='那未来会继续看好腾讯吗？我们当年买入后一直持有。后来我们持有的时间超过了十年。')]


def test_skip_can_only_trigger_one_stricter_company_review_with_original_evidence(tmp_path):
    cues=holding();before=deepcopy(cues);calls=[]
    def call(prompt,schema):
        calls.append((prompt,schema))
        if 'candidate_0' in schema['properties']:
            return dict(candidate_0=dict(kind='skip',subject_sentence=0,event_sentence=1,detail_sentence=2))
        assert list(schema['properties'])==['subject_sentence','event_sentence','detail_sentence','kind']
        assert schema['properties']['kind']['enum']==['skip','company_case']
        return dict(subject_sentence=0,event_sentence=1,detail_sentence=2,kind='company_case')
    result=E.choose_stories(cues,[dict(start=0,end=0)],call,tmp_path/'report.json')
    assert len(calls)==2 and result['selection_completed'] and cues==before
    row=result['selected'][0]['story_selection']
    assert row['kind']=='company_case' and row['company_holding_recall_version']==1
    assert row['event_quote']=='我们当年买入后一直持有。'
    assert result['calls'][-1]['stage']=='company_holding_recall_v1'
    assert json.loads((tmp_path/'report.json').read_text())['calls'][-1]['response']['kind']=='company_case'


@pytest.mark.parametrize('text',[
    '我将来会买入腾讯。','如果我当年买入腾讯就能赚钱。',
    '您当年买入腾讯后持有。','我当年考虑买入腾讯。','我当年并未买入腾讯。',
    '我过去没有持有腾讯。','只要我们当年买了腾讯就行了。','我们应该已经持有腾讯。',
])
def test_future_hypothetical_denied_host_premise_and_advice_cannot_be_recalled(text):
    assert E.company_past_event_error(text)
    assert E.company_holding_request([dict(text='您买过腾讯吗？'+text+'后来一直关注公司发展。')],dict(start=0,end=0)) is None


def test_no_named_company_and_no_real_past_action_do_not_add_model_calls():
    assert E.company_holding_request([dict(text='您怎么看医药行业？我们当年买入后持有。后来持有超过十年。')],dict(start=0,end=0)) is None
    assert E.company_holding_request([dict(text='您怎么看腾讯？未来也许会买入。现在还需要观察。')],dict(start=0,end=0)) is None


def test_recall_cannot_return_a_personal_story_or_unexecuted_action(tmp_path):
    for verdict in [dict(kind='personal_story',subject_sentence=0,event_sentence=1,detail_sentence=2),
                    dict(kind='company_case',subject_sentence=0,event_sentence=2,detail_sentence=1)]:
        cues=[dict(text='那未来会继续看好腾讯吗？我们当年买了腾讯。我们现在继续持有。未来我们应该会持有更长时间。')]
        def call(prompt,schema):
            if 'candidate_0' in schema['properties']:
                return dict(candidate_0=dict(kind='skip',subject_sentence=0,event_sentence=1,detail_sentence=2))
            return verdict
        result=E.choose_stories(cues,[dict(start=0,end=0)],call,tmp_path/'report.json')
        assert not result['selected'] and result['unresolved'] and not result['selection_completed']


def test_recall_service_failure_stays_unresolved_and_does_not_erase_other_successes(tmp_path):
    def call(prompt,schema):
        if 'candidate_0' in schema['properties']:
            return dict(candidate_0=dict(kind='skip',subject_sentence=0,event_sentence=1,detail_sentence=2))
        raise RuntimeError('Temporary classifier failure')
    result=E.choose_stories(holding(),[dict(start=0,end=0)],call,tmp_path/'report.json')
    assert result['retryable'] and not result['selection_completed'] and len(result['calls'])==2


def test_actual_false_positive_cross_sentence_hypothesis_cannot_be_extracted():
    text='您怎么看腾讯？我们先做一个假设。我当年买了腾讯。我们现在仍然持有。刚才说的是假设的持仓，并不是我们的实际交易。'
    assert E.company_past_event_error('我当年买了腾讯。') is None
    assert E.company_holding_detail_error('我们现在仍然持有。') is None
    assert E.company_holding_context_error(text)
    assert E.company_holding_request([dict(text=text)],dict(start=0,end=0)) is None


def test_independent_publisher_replays_full_original_context_before_accepting_recall(tmp_path):
    import caption_readability as C
    import title_rewrite as T
    from fc import index as F
    raw='您怎么看腾讯？我们先做一个假设。我当年买了腾讯。我们现在仍然持有。刚才说的是假设的持仓，并不是我们的实际交易。'
    _,proof=C.clean_entries([dict(start_sec=0,end_sec=30,zh=raw)])
    name='subtitle_edit_proof_1.json'
    (tmp_path/name).write_text(json.dumps(proof,ensure_ascii=False))
    (tmp_path/'subtitles.ass').write_text('Dialogue: 0,0:00:00.00,0:00:30.00,Default,,0,0,0,,'+proof['display_text'])
    # Even a completely replayable subtitle proof and an old all-green title
    # approval cannot turn the quoted hypothetical trade into an actual one.
    import hashlib
    meta=dict(title='林园：腾讯买入后一直持有',subtitle_files=['subtitles.ass'],
        subtitle_text_sha256=F.editorial.text_digest(proof['display_text']),
        subtitle_edit_proof_version=1,subtitle_edit_proofs=[name],
        title_rewrite=dict(source_sha256=hashlib.sha256(T.compact(raw).encode()).hexdigest()),
        experiment_proof=dict(selection=dict(company_holding_recall_version=1)))
    assert 'hypothetical' in F.artifact_subtitle_error(meta,tmp_path)
    meta.pop('title_rewrite')
    assert '完整原始字幕' in F.artifact_subtitle_error(meta,tmp_path)



def test_actual_holding_title_keeps_past_duration_and_cannot_borrow_host_future_pledge():
    selection=dict(kind='company_case',subject_quote='那这未来会继续看好茅台？',
        event_quote='从买入的这个持有到现在已经接近二十年，接近二十年啊。',
        detail_quote='我们也不会动。',company_holding_recall_version=1)
    focus=E.title_focus(selection)
    assert E.title_focus_error('林园：继续持有茅台至少未来几年甚至终身',focus)
    assert E.title_focus_error('林园：茅台持有二十年，我们也不会动',focus)
    assert E.title_focus_error('林园：茅台持有近20年，甚至终身都不动',focus)
    assert E.title_focus_error('林园：茅台持有近20年，我们也不会动',focus) is None
    assert E.title_focus_error('林园：茅台持有近二十年，可能终身也不动',focus) is None
    assert E.title_focus_error('林园：茅台持有近20年，我们也不会动',focus,cover='茅台永远不卖')


def test_holding_title_focus_reaches_real_title_generator_without_breaking_source_word_contract(monkeypatch):
    import title_rewrite as T
    evidence=dict(kind='company_case',company_holding_recall_version=1,
        subject_quote='那这未来会继续看好茅台？',
        event_quote='从买入的这个持有到现在已经接近二十年，接近二十年啊。',
        detail_quote='我们也不会动。')
    source=''.join(evidence[k] for k in ('subject_quote','event_quote','detail_quote'))
    focus=E.title_focus(evidence)
    class ModelReached(Exception):pass
    def call(*args):raise ModelReached()
    with pytest.raises(ModelReached):
        T.generate(source,structured_model=call,editorial_focus=focus)
