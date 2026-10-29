"""A real rejected story must not be forced to title generic narration nouns."""
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'linyuan'))
import publishing_experiment as E

FIXTURE = Path(__file__).with_name('fixtures') / 'linyuan_story_title_20261029.json'


def test_actual_failed_story_keeps_all_original_context_and_specific_company():
    x=json.loads(FIXTURE.read_text());s=x['story_evidence']
    assert len(x['source_cues'])==43
    assert E.digest(''.join(x['source_cues']))==s['transcript_sha256']
    assert all(s[k] in ''.join(x['source_cues']) for k in ('subject_quote','event_quote','detail_quote'))
    focus=E.title_focus(s)
    assert focus['subjects']==['招商银行'] and focus['details']==[]
    title='林园：招商银行两块多时，我就说到十五块才是牛市'
    assert E.title_focus_error(title,focus) is None
    assert E.title_focus_error(title.replace('招商银行','银行板块'),focus)


def test_prompt_does_not_replace_source_detail_coverage_with_fact_approval():
    x=json.loads(FIXTURE.read_text())
    prompt=E.story_title_prompt('',dict(properties=dict(c_candidates={})),x['story_evidence'])
    assert '记录、结果' in prompt and '条件' in prompt and '独立复核' in prompt
    assert '不改成简称' in prompt


def test_named_decision_standard_still_has_to_appear_in_the_title():
    s=dict(kind='company_case',subject_quote='当年我投资了茅台。',event_quote='我当时采用消费升级理论。',
           detail_quote='当时我们的收入达到了一定水平。')
    focus=E.title_focus(s)
    assert focus['details'] and '消费升级' in ''.join(focus['details'])
    assert E.title_focus_error('林园：当年投茅台，我看的是消费升级',focus) is None
    assert E.title_focus_error('林园：当年投茅台，后来赚了很多钱',focus)
