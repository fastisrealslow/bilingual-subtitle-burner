import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'linyuan'))
import publishing_experiment as E


def test_actual_skipped_source_preserves_every_candidate_and_evidence_first_order():
    f = json.loads((ROOT / 'tests/fixtures/linyuan_actual_skipped_life_story.json').read_text())
    assert f['original_cues_sha256'] == f['actual_old_report']['transcript_sha256']
    assert E.digest(f['cues']) == f['classifier_cues_sha256']
    for record in f['actual_old_report']['calls']:
        rows = json.loads(record['prompt'].rsplit('\n', 1)[-1])
        for original, row in zip(record['indices'], rows, strict=True):
            assert E._selection_rows(f['cues'], [f['candidates'][original]])[0]['sentences'] == row['sentences']
    assert len(f['candidates']) == 9 and not f['actual_old_report']['selected']
    prompt, schema = E.selection_request(f['cues'], [f['candidates'][6]])
    fields = schema['properties']['candidate_0']
    assert list(fields['properties']) == ['subject_sentence', 'event_sentence', 'detail_sentence', 'kind']
    assert '最后再填kind' in prompt and '不限于买卖交易' in prompt
    assert '七八万' in prompt and '住了十天的院' in prompt
    rows = E._selection_rows(f['cues'], [f['candidates'][6]])
    assert rows[0]['text'] == ''.join(c['text'] for c in f['cues'][382:404])
    # No example answer, title, source correction or prescribed IDs in prompt.
    assert 'kind为personal_story、company_case或skip' in prompt


def test_fixed_experiment_and_actual_event_vetoes_unchanged():
    assert E.VERSION == 2026101801 and E.SELECTION_REVISION == 2026102201
    assert E.START == '2026-10-18' and E.MIN_MOTHER_SECONDS == 1200
    assert E.personal_event_error('我自己觉得是没问题。')
    assert E.personal_event_error('再爬起来就行了。')
    assert E.personal_detail_error('五年以后就能见分晓。')


def test_actual_false_personal_selections_rejected_without_erasing_real_life_event():
    f = json.loads((ROOT / 'tests/fixtures/linyuan_actual_false_personal_stories.json').read_text())
    for pick in f['false_selections']:
        evidence = pick['story_selection']
        assert E.personal_event_error(evidence['event_quote']) or E.personal_detail_error(evidence['detail_quote'])
    assert E.personal_detail_error('有的人他想好了，我今天投十万，明天投十万，结果他不敢投了。')
    assert not E.personal_event_error('我当时选择了医药行业。')
    assert not E.personal_detail_error('去年有个人问我为什么退出房地产，我跟他讲过原因。')
    assert not E.personal_event_error('我去了以后，他就不舒服了，啊，就心衰，送到那个国际医学去看病。')
    assert not E.personal_detail_error('就这，人家国际医学还给我妈，他，在那住了十天的院，都还给免费了。')


def test_schema_excludes_proven_generic_method_event_ids_without_hiding_source_text():
    f = json.loads((ROOT / 'tests/fixtures/linyuan_actual_skipped_life_story.json').read_text())
    for index in (0, 4):
        picks = [f['candidates'][index]]
        rows = E._selection_rows(f['cues'], picks)
        prompt, schema = E.selection_request(f['cues'], picks)
        allowed = schema['properties']['candidate_0']['properties']['event_sentence']['enum']
        bad = [s for s in rows[0]['sentences'] if E.generic_method_event_error(s['text'])]
        assert bad and all(s['id'] not in allowed for s in bad)
        assert all(s['text'] in prompt for s in bad)
        assert rows[0]['text'] == ''.join(c['text'] for c in f['cues'][picks[0]['start']:picks[0]['end']+1])
    # Past company-choice explanations are not excluded merely for describing
    # the actual standard used for that company.
    assert not E.generic_method_event_error('我们当时提出了一个理论是消费升级啊，就投消费升级。')
    assert not E.generic_method_event_error('我们当时的方法是必须先研究现金流。')


def test_schema_with_only_generic_method_event_must_skip_instead_of_empty_enum():
    cues = [dict(text='我的方法有钱就投股票。')]
    _, schema = E.selection_request(cues, [dict(start=0, end=0)])
    prop = schema['properties']['candidate_0']['properties']
    assert prop['kind']['enum'] == ['skip']
    assert prop['event_sentence']['enum']
