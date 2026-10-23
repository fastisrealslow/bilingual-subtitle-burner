"""Replay all actual source candidates; classification alone never approves media."""
import json
from pathlib import Path
import time
import urllib.request


def main():
    import produce_cn as P
    import publishing_experiment as E
    assert P.TEXT_BACKEND == 'local' and P.LOCAL_LLM_MODEL == 'qwen3.5:9b' and P.automatic_only()
    with urllib.request.urlopen('http://127.0.0.1:11434/api/tags', timeout=15) as response:
        tags = json.load(response)
    digest = next(m['digest'] for m in tags['models'] if m['name'] == P.LOCAL_LLM_MODEL)
    assert digest == '56671c2ab9385f9cfcb404638e32cd62d88e3501d44822208363c010179a3c90'
    fixture = json.loads(Path('tests/fixtures/linyuan_actual_skipped_life_story.json').read_text())
    cues = fixture['cues']; candidates = fixture['candidates']; old = fixture['actual_old_report']
    assert fixture['original_cues_sha256'] == old['transcript_sha256']
    assert E.digest(cues) == fixture['classifier_cues_sha256']
    # Old actual model inputs establish exact equality of every complete
    # candidate's source text. Only unused character alignment arrays are omitted.
    checked = set()
    for record in old['calls']:
        actual_rows = json.loads(record['prompt'].rsplit('\n', 1)[-1])
        for original, row in zip(record['indices'], actual_rows, strict=True):
            current = E._selection_rows(cues, [candidates[original]])[0]
            assert current['sentences'] == row['sentences']
            checked.add(original)
    assert checked == set(range(len(candidates)))
    assert old['candidate_count'] == len(candidates) == 9 and not old['selected']
    out = Path('story-evidence-order-verification'); out.mkdir(exist_ok=True)
    result = dict(passed=False, source_run_id=fixture['source_run_id'],
        source_sha256=fixture['source_sha256'], model_digest=digest,
        old_actual_selected=0, diagnostic_only=True, asr_approved=False,
        media_approved=False, registered_stock=False, published=False)
    def save(): (out / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
    save()
    deadline = time.monotonic() + 1200
    def call(prompt, schema):
        remaining = deadline - time.monotonic()
        if remaining <= 0: raise RuntimeError('Actual source selection budget exhausted')
        return P.llm([dict(role='user', content=prompt)], '', temperature=0,
            max_tokens=1200, budget_sec=min(600, remaining), response_schema=schema)
    report = E.choose_stories(cues, candidates, call, out / 'actual-source.json', budget_sec=1200)
    result['new_selected'] = report['selected']; save()
    assert report['selection_completed'] and not report['retryable']
    positive = candidates[6]; negative = candidates[8]
    assert any(p['start'] == positive['start'] and p['end'] == positive['end']
        and p['story_selection']['kind'] == 'personal_story' for p in report['selected'])
    assert not any(p['start'] == negative['start'] and p['end'] == negative['end'] for p in report['selected'])
    # An already published actual company-answer case remains a positive.
    company = json.loads(Path('tests/fixtures/linyuan_story_published_company_control.json').read_text())
    cc = company['cues']; cp = [dict(start=0, end=len(cc)-1)]
    deadline = time.monotonic() + 600
    control = E.choose_stories(cc, cp, call, out / 'published-company.json', budget_sec=600)
    assert control['selection_completed'] and len(control['selected']) == 1
    assert control['selected'][0]['story_selection']['kind'] == 'company_case'
    assert E.digest(cues) == fixture['classifier_cues_sha256']
    result.update(passed=True, generic_future_remains_rejected=True,
        published_company_still_selected=True)
    save(); print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__': main()
