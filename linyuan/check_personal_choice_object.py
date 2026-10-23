"""Fresh full-title review of an independently machine-selected real choice."""
import hashlib
import json
from pathlib import Path

from check_company_subject_guard import archive


def read_suffix(z,suffix):
    names=[n for n in z.namelist() if n.endswith(suffix)]
    assert len(names)==1
    return json.loads(z.read(names[0]))


def main():
    import produce_cn as P
    import publishing_experiment as E
    import title_rewrite as T
    assert P.TEXT_BACKEND=='local' and P.LOCAL_LLM_MODEL=='qwen3.5:9b'
    out=Path('personal-choice-object-verification');out.mkdir(exist_ok=True)
    report=dict(passed=False,diagnostic_only=True,media_approved=False,
        registered_stock=False,published=False,audio_verified=False,
        source_cues_changed=False,model=P.LOCAL_LLM_MODEL)
    def save():
        (out/'verification.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    save()
    try:
        with archive(12475677396,'46d886a85f93beca464da8a1cec7c450c1fe4550bae6f74f49a4fa0e617ae85e') as z:
            prior=read_suffix(z,'verification.json')
            selected=read_suffix(z,'fresh-selection.json')['selected']
        assert prior['passed'] and prior['actual_choice_reclassified'] and len(selected)==1
        with archive(12473058525,'04725d4d4f8d3a370c599633ecce8d37c7d8dd84830f195f4129c5351de76d24') as z:
            source=json.loads(z.read('_tmp/source_quality.json'))
            cues=json.loads(z.read('_tmp/cues_raw.json'))
        assert source['source_sha256']==prior['source_sha256']
        before=E.digest(cues);item=selected[0];evidence=item['story_selection']
        assert evidence['kind']=='personal_story' and evidence==prior['actual_story_evidence']
        # Preserve the prior independently generated range/kind/quotes exactly;
        # this is only a title diagnostic, never a new production selection.
        validated=E.validate_choices(cues,[item],dict(choices=[dict(index=0,**evidence)]))
        assert validated[0]['story_selection']==evidence
        focus=E.title_focus(evidence)
        assert focus['subjects']==['银行']
        assert E.title_focus_error(prior['actual_title'],focus)
        report.update(old_title=prior['actual_title'],old_objectless_title_rejected=True,
            source_sha256=source['source_sha256'],actual_selection_unchanged=True,
            experiment_code_sha256=hashlib.sha256(Path(E.__file__).read_bytes()).hexdigest())
        save()
        work=out/'actual-title';work.mkdir(exist_ok=True)
        copy=P.copywrite(cues,list(range(item['start'],item['end']+1)),
            '林园','访谈','',work,'_actual',story_evidence=evidence,
            source_sha256=source['source_sha256'])
        text=''.join(c['text'] for c in cues[item['start']:item['end']+1])
        assert T.error(copy['title'],copy['title_rewrite'],text) is None
        assert E.title_focus_error(copy['title'],focus,cover=copy['cover_title']) is None
        assert copy['title_rewrite']['review']['method']=='cpu_text_review'
        report.update(new_title=copy['title'],new_cover_title=copy['cover_title'],
            actual_full_title_generation_review_passed=True)
        save()
        with archive(12440338758,'6b009b88b23995ab55bc219b7a07d4bd03ea83c71e70868f8f52f09530b771a8') as z:
            accepted=json.loads(z.read('meta.json'))
        assert E.proof_error(accepted) is None
        report.update(accepted_real_company_preserved=True,
            source_cues_changed=before!=E.digest(cues))
        report['passed']=not report['source_cues_changed']
        save()
    except Exception as exc:
        report.update(error=type(exc).__name__+': '+str(exc),passed=False);save();raise
    print(json.dumps(report,ensure_ascii=False,indent=2),flush=True)
    assert report['passed'],'Object omission repair failed; do not promote'


if __name__=='__main__':main()
