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
