"""Fresh current-model reclassification/title test; never media or stock approval."""
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import zipfile


def archive(artifact,expected=None):
    raw=subprocess.check_output(['gh','api',
        f'repos/{os.environ["GITHUB_REPOSITORY"]}/actions/artifacts/{artifact}/zip'],timeout=180)
    if expected and hashlib.sha256(raw).hexdigest()!=expected:
        raise ValueError('Immutable source evidence changed')
    return zipfile.ZipFile(io.BytesIO(raw))


def main():
    import produce_cn as producer
    import publishing_experiment as E
    import title_rewrite as title
    assert producer.TEXT_BACKEND=='local' and producer.LOCAL_LLM_MODEL=='qwen3.5:9b'
    assert producer._copy_style_identity('林园')['title_draft_profile']=='production'
    out=Path('company-subject-guard-verification');out.mkdir(exist_ok=True)
    report=dict(passed=False,diagnostic_only=True,media_approved=False,
        registered_stock=False,published=False,sample_registered=False,audio_verified=False,
        model=producer.LOCAL_LLM_MODEL,source_cues_changed=False)
    def save():
        (out/'verification.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    save()
    try:
        with archive(12473058525,'04725d4d4f8d3a370c599633ecce8d37c7d8dd84830f195f4129c5351de76d24') as z:
            source=json.loads(z.read('_tmp/source_quality.json'))
            cues=json.loads(z.read('_tmp/cues_raw.json'))
            prior=json.loads(z.read('_tmp/story_selection.json'))
        assert source['source_sha256']=='37bdc1449e1ce46a3df375f012cce6f3c1213b51da7f1afe7a6bb0c7da69f7a9'
        assert E.digest(cues)==prior['transcript_sha256']
        candidates=prior['candidates'];before=E.digest(cues)
        # Diagnose the original MACHINE-selected failure, not a supplied edit
        # list. All original12 complete candidates are independently reviewed.
        affected=[p for p in prior['selected'] if E.company_subject_error(p['story_selection'])]
        assert len(candidates)==12 and len(affected)==1
        report.update(source_run=39883678616,source_artifact=12473058525,
            source_sha256=source['source_sha256'],original_candidates=12,
            old_invalid_subject=affected[0]['story_selection'],
            old_invalid_rejected=True,
            experiment_code_sha256=hashlib.sha256(Path(E.__file__).read_bytes()).hexdigest())
        save()
        def call(prompt,schema):
            return producer.llm([dict(role='user',content=prompt)],'',temperature=0,
                max_tokens=1600,budget_sec=producer.text_budget(240),
                response_schema=schema,read_cache=False)
        selected=E.choose_stories(cues,candidates,call,out/'fresh-selection.json')
        target=[p for p in selected['selected'] if any(
            p['start']==old['start'] and p['end']==old['end'] for old in affected)]
        report.update(selection_completed=selected['selection_completed'],
            actual_choice_reclassified=bool(len(target)==1 and target[0]['story_selection']['kind']=='personal_story'))
        save()
        assert report['selection_completed'] and report['actual_choice_reclassified'],\
            'The same complete actual source did not independently qualify as a personal choice'
        item=target[0];work=out/'actual-title';work.mkdir(exist_ok=True)
        copy=producer.copywrite(cues,list(range(item['start'],item['end']+1)),
            '林园','访谈','',work,'_actual',story_evidence=item['story_selection'],
            source_sha256=source['source_sha256'])
        text=''.join(c['text'] for c in cues[item['start']:item['end']+1])
        assert title.error(copy['title'],copy['title_rewrite'],text) is None
        assert E.title_focus_error(copy['title'],E.title_focus(item['story_selection']),cover=copy['cover_title']) is None
        assert copy['title_rewrite']['review']['method']=='cpu_text_review'
        assert not any(word in copy['title'] for word in ('谢谢','林总'))
        report.update(actual_title=copy['title'],actual_cover_title=copy['cover_title'],
            actual_title_full_generation_review_passed=True,actual_story_evidence=item['story_selection'])
        save()
        # Protect the already-accepted real company example under both the
        # current proof verifier AND a fresh classifier call with unchanged raw
        # evidence. Locate it by its accepted digest, never a supplied range.
        with archive(12440338758,'6b009b88b23995ab55bc219b7a07d4bd03ea83c71e70868f8f52f09530b771a8') as z:
            accepted=json.loads(z.read('meta.json'))
        assert E.proof_error(accepted) is None
        with archive(12428005891,'3a672e0efb52e302e70acb98ab92b4608e83ffb8eecf45dbdf5a2d6d5748b119') as z:
            old_cues=json.loads(z.read('_tmp/cues_raw.json'))
            old_source=json.loads(z.read('_tmp/source_quality.json'))
            old_candidates=json.loads(z.read('_tmp/story_selection.json'))['candidates']
        assert old_source['source_sha256']==accepted['source_sha256']
        digest=accepted['experiment_proof']['selection']['transcript_sha256']
        positive=[p for p in old_candidates if E.digest(''.join(
            c['text'] for c in old_cues[p['start']:p['end']+1]))==digest]
        assert len(positive)==1
        preserved=E.choose_stories(old_cues,positive,call,out/'real-company-regression.json')
        assert preserved['selection_completed'] and len(preserved['selected'])==1
        assert preserved['selected'][0]['story_selection']['kind']=='company_case'
        report['accepted_real_company_preserved']=True
        save()
        negative_texts=[
            '谢谢林总，网友说银行估值很低，可以抄底吗？我们只谈行业趋势，没有具体的个人买入或退出经历。',
            '如果将来买银行，我会先看风险。这里只是在提出假设，并没有讲任何已经发生的投资行为。',
            '您当年为什么买了茅台？这个问题我们先不讨论，下面请下一位朋友提问。',
            '医药和银行未来二十年谁会更好？我们只在比较未来的可能性，没有回顾具体的个人经历。',
        ]
        negative_cues=[dict(text=t) for t in negative_texts]
        negatives=E.choose_stories(negative_cues,[dict(start=i,end=i) for i in range(4)],
            call,out/'controlled-negative-selection.json')
        report['negative_controls_passed']=bool(negatives['selection_completed'] and not negatives['selected']
            and len(negatives['rejected'])==4)
        report['source_cues_changed']=before!=E.digest(cues)
        report['passed']=bool(report['negative_controls_passed'] and not report['source_cues_changed']
            and report['accepted_real_company_preserved'] and report['actual_title_full_generation_review_passed'])
        save()
    except Exception as exc:
        report.update(error=type(exc).__name__+': '+str(exc),passed=False);save();raise
    print(json.dumps(report,ensure_ascii=False,indent=2),flush=True)
    assert report['passed'],'Actual company-subject correction did not pass; do not promote'


if __name__=='__main__':main()
