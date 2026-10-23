"""Replay actual zero-candidate sources through bounded proposals and argument QA."""
import argparse
import json
import os
from pathlib import Path
import tempfile
import zipfile

from check_scene_cover_borders import fetch
import keynote_proposals as K
import source_selection as S


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--artifact-id',type=int,required=True,choices=(12488688653,12488766515))
    args=parser.parse_args()
    import produce_cn as P
    assert P.TEXT_BACKEND=='local' and P.LOCAL_LLM_MODEL=='qwen3.5:9b' and P.automatic_only()
    expected={12488688653:('BV1dDWVeUExM:p1','eecae74ac60a5ccb1e52bff1e079c99de25a41f9c24df75f0ef416af2f7c19f9'),
              12488766515:('BV1hS421X7Xd:p2','b03934fb5cc4a1a87f68e3c146d5679d6952df05108d44501306af58a293bcf4')}
    source_id,source_sha=expected[args.artifact_id]
    out=Path('keynote-proposal-verification');out.mkdir(exist_ok=True)
    result=dict(passed=False,source_id=source_id,source_sha256=source_sha,
        original_artifact_id=args.artifact_id,diagnostic_only=True,
        registered_stock=False,published=False,media_approved=False,argument_reviews=[])
    def save():(out/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    save()
    with tempfile.TemporaryDirectory(prefix='keynote-actual-') as tmp:
        archive=Path(tmp)/'source.zip';fetch(args.artifact_id,archive)
        with zipfile.ZipFile(archive) as z:
            cues=json.loads(z.read('_tmp/cues_raw.json'))
            source=json.loads(z.read('_tmp/source_quality.json'))
        assert source['source_sha256']==source_sha and source['passed'] is True
    original=K.digest(cues)
    result['original_cues_sha256']=original
    (out/'original-cues.json').write_text(json.dumps(cues,ensure_ascii=False,indent=2))
    old={};assert S.select(cues,whole_source=True,limit=None,diagnostics=old)==[]
    result['control']=old
    def call(prompt,schema,budget):
        return P.llm([dict(role='user',content=prompt)],'',temperature=0,max_tokens=256,
            budget_sec=budget,response_schema=schema,read_cache=False)
    report=K.propose(cues,source,call,out/'keynote_proposals.json')
    result.update(proposal_count=len(report['picks']),selection_completed=report['selection_completed'],
                  proposal_windows=len(report['calls']),unresolved=report['unresolved'])
    save()
    assert report['selection_completed'] and report['picks'],'Real source yielded no completed valid proposal'
    # The proposal never supplies a semantic pass. Exercise the unchanged real
    # automatic argument reader on the first three candidates, stopping only
    # after an independently grounded positive. Failed candidates remain here.
    for index,pick in enumerate(report['picks'][:3]):
        record=dict(start=pick['start'],end=pick['end'],passed=False)
        try:
            proof=P.argument_record_for_render(cues,[pick],'林园','',out,f'_actual_{index}')
            assert not P.editorial.model_review_skipped(proof)
            assert P.editorial.review_error(proof) is None
            record.update(passed=True,proof=proof)
        except Exception as exc:record['error']=f'{type(exc).__name__}: {exc}'
        result['argument_reviews'].append(record);save()
        if record['passed']:break
    assert K.digest(cues)==original,'Original ASR was modified'
    result['argument_review_passed']=any(row['passed'] for row in result['argument_reviews'])
    result['passed']=result['argument_review_passed']
    save()
    print(json.dumps({k:v for k,v in result.items() if k not in ('control','argument_reviews')},ensure_ascii=False))
    assert result['passed'],'No candidate passed the unchanged independent argument reader'


if __name__=='__main__':main()
