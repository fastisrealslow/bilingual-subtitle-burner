"""Fresh 1.7B transcript role checks; no preselected range or title inputs."""
import hashlib,json,os,subprocess,tempfile,zipfile
from pathlib import Path


def main():
    import produce_cn as P
    import publishing_experiment as E
    import source_selection as S
    from transcript_audit import review
    assert P.TEXT_BACKEND=='local' and P.LOCAL_LLM_MODEL=='qwen3.5:9b'
    S.editorial.CONTENT_POLICY='reference_v1';S.editorial.MIN_SECONDS=20
    root=Path('primary-company-holding-check');root.mkdir(exist_ok=True)
    result=dict(passed=False,diagnostic_only=True,registered_stock=False,published=False,media_approved=False,
        source_artifact_id=12528305942,policy_sha256=hashlib.sha256(Path(E.__file__).read_bytes()).hexdigest())
    def save():(root/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    save()
    with tempfile.TemporaryDirectory() as tmp:
        p=Path(tmp)/'actual.zip'
        with p.open('wb') as f:subprocess.run(['gh','api',f'repos/{os.environ["GITHUB_REPOSITORY"]}/actions/artifacts/12528305942/zip'],stdout=f,check=True,timeout=180)
        with zipfile.ZipFile(p) as z:
            cues=json.loads(z.read('_tmp/cues_raw.json'));source=json.loads(z.read('_tmp/source_quality.json'))
            raw=json.loads(z.read('_tmp/asr_raw_chunks.json'))
    assert source['source_sha256']=='6f5ddecc6db4f2045e37a83f63a7d3a122f08287ee1258e9c6f085abdb2b9c2d'
    assert raw and all(r['model_id']=='Qwen/Qwen3-ASR-1.7B' and r['source_video_sha256']==source['source_sha256'] for r in raw)
    before=E.digest(cues);parents=S.select(cues,limit=None,whole_source=True)
    candidates=[]
    for p in parents:
        rows=E._selection_rows(cues,[p])
        if not rows:continue
        units=rows[0]['sentences']
        if (any(E.future_holding_subject(r['text']) and not E.company_subject_error(dict(kind='company_case',subject_quote=r['text'])) for r in units)
                and any(E.company_past_event_error(r['text']) is None for r in units)):
            candidates.append(p)
    result.update(all_natural_source_candidates=len(parents),literal_holding_candidates=len(candidates),raw_source_unchanged=before==E.digest(cues))
    save();print('stage=actual_selection',flush=True);assert candidates
    def call(prompt,schema):return P.llm([dict(role='user',content=prompt)],'',temperature=0,max_tokens=1600,
        budget_sec=P.text_budget(240),response_schema=schema,read_cache=False)
    chosen=E.choose_stories(cues,candidates,call,root/'actual-primary-selection.json')
    assert chosen['selection_completed'] and chosen['selected']
    result['stage']='negative_controls';save();print('stage=negative_controls',flush=True)
    texts=[
      '那未来会继续投资腾讯吗？未来有可能会买，但现在还没买入。我们还需要看看业务，暂时没有实际决定。',
      '那未来会继续持有茅台吗？如果我当年买了并持有二十年，今天就会有这样的收益。但这只是一个假设例子，我没有这笔持仓。',
      '那未来会继续看好腾讯吗？我们当年买入后一直持有。那就是说至少未来五年还会继续持有腾讯。',
      '那未来会继续看好腾讯吗？我们先做一个假设。我当年买了腾讯。我们现在仍然持有。刚才说的是假设的持仓，并不是我们的实际交易。',
    ]
    neg=E.choose_stories([dict(text=t) for t in texts],[dict(start=i,end=i) for i in range(len(texts))],call,root/'negative-controls.json')
    result.update(negative_controls=len(texts),negative_completed=neg['selection_completed'],negative_selected=len(neg['selected']))
    save();assert neg['selection_completed'] and not neg['selected']
    outputs=[]
    for n,pick in enumerate(chosen['selected']):
        evidence=pick['story_selection'];assert evidence['kind']=='company_case' and E.holding_evidence(evidence)
        assert E.company_holding_detail_error(evidence['detail_quote']) is None
        transcript=''.join(c['text'] for c in cues[pick['start']:pick['end']+1])
        result['stage']='transcript_audit';save();print('stage=transcript_audit',flush=True)
        audit=review(transcript,'林园',P.LOCAL_LLM_MODEL,call,root/f'actual-{n}-audit.json')
        assert audit['passed'],audit['issues']
        os.environ['LINYUAN_EDITORIAL_PROFILE']=E.TREATMENT
        work=root/f'copy-{n}';work.mkdir(exist_ok=True)
        result['stage']='actual_title';save();print('stage=actual_title',flush=True)
        copy=P.copywrite(cues,list(range(pick['start'],pick['end']+1)),'林园','访谈','',work,
            '_actual',story_evidence=evidence,source_sha256=source['source_sha256'])
        assert E.title_focus_error(copy['title'],E.title_focus(evidence),cover=copy['cover_title']) is None
        outputs.append(dict(start=pick['start'],end=pick['end'],selection=evidence,title=copy['title'],cover_title=copy['cover_title'],audit_passed=True))
    result.update(passed=bool(outputs) and before==E.digest(cues),outputs=outputs,raw_source_unchanged=before==E.digest(cues))
    save();print(json.dumps(result,ensure_ascii=False),flush=True);assert result['passed']


if __name__=='__main__':main()
