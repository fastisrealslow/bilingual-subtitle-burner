"""Same nine actual candidates, current real CPU selector, no media approval."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import zipfile


class RecordedCalls:
    """Exact immutable model-response cache, never cached approval flags."""
    def __init__(self,report):
        self.calls=report['calls'];self.index=0

    def __call__(self,prompt,schema):
        if self.index>=len(self.calls):raise AssertionError('Actual classifier replay exhausted')
        row=self.calls[self.index]
        assert row['prompt']==prompt and row['schema']==schema,'Classifier prompt/schema changed; fresh model trial required'
        assert 'response' in row and not row.get('error'),'Cannot reuse incomplete model evidence'
        self.index+=1
        return row['response']

    def finish(self):
        assert self.index==len(self.calls),'Current selector did not consume the exact actual evidence'


def load_original(archive):
    import publishing_experiment as experiment
    with zipfile.ZipFile(archive) as z:
        cues=json.loads(z.read('_tmp/cues_raw.json'))
        original=json.loads(z.read('_tmp/story_selection.json'))
        quality=json.loads(z.read('_tmp/source_quality.json'))
    assert quality['source_sha256']=='6f5ddecc6db4f2045e37a83f63a7d3a122f08287ee1258e9c6f085abdb2b9c2d'
    assert original['transcript_sha256']==experiment.digest(cues)
    candidates=original['candidates']
    assert len(candidates)==9 and original['selected']==[]
    assert {(p['start'],p['end']) for p in candidates}>={(76,87),(333,360),(106,123)}
    # Ensure the same complete evidence, not a shortened or handpicked input.
    for call in original['calls']:
        prompt,schema=experiment.selection_request(cues,[candidates[i] for i in call['indices']])
        units=json.loads(prompt.split('\n',1)[1])
        previous=json.loads(call['prompt'].split('\n',1)[1])
        assert units==previous and schema==call['schema']
    return cues,candidates,quality


def main():
    import produce_cn as production
    import publishing_experiment as experiment
    assert production.TEXT_BACKEND=='local' and production.LOCAL_LLM_MODEL=='qwen3.5:9b'
    target=Path('company-qa-selection-verification');target.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='company-qa-original-') as tmp:
        archive=Path(tmp)/'actual.zip'
        with archive.open('wb') as handle:
            subprocess.run(['gh','api',f'repos/{os.environ["GITHUB_REPOSITORY"]}/actions/artifacts/12428005891/zip'],
                stdout=handle,check=True,timeout=180)
        cues,candidates,quality=load_original(archive)
        replay={}
        artifact=os.environ.get('STORY_CLASSIFIER_REPLAY_ARTIFACT','')
        if artifact:
            assert artifact=='12433790083','Only the inspected actual classifier evidence may be reused'
            metadata=json.loads(subprocess.check_output(['gh','api',
                f'repos/{os.environ["GITHUB_REPOSITORY"]}/actions/artifacts/{artifact}'],timeout=90))
            assert metadata['workflow_run']['id']==39792050300
            assert metadata['workflow_run']['head_sha']=='0272ae96b18394731a0a85f2547571e3012afe7b'
            cached=Path(tmp)/'classifier.zip'
            with cached.open('wb') as handle:
                subprocess.run(['gh','api',f'repos/{os.environ["GITHUB_REPOSITORY"]}/actions/artifacts/{artifact}/zip'],
                    stdout=handle,check=True,timeout=180)
            with zipfile.ZipFile(cached) as z:
                for name in ('story_selection.json','controlled-negative-selection.json'):
                    paths=[p for p in z.namelist() if p.endswith('/'+name)]
                    assert len(paths)==1
                    replay[name]=json.loads(z.read(paths[0]))
            assert replay['story_selection.json']['transcript_sha256']==experiment.digest(cues)
    def call(prompt,schema):
        return production.llm([dict(role='user',content=prompt)],'',temperature=0,
            max_tokens=1600,budget_sec=production.text_budget(240),response_schema=schema,read_cache=False)
    cached_call=RecordedCalls(replay['story_selection.json']) if replay else None
    report=experiment.choose_stories(cues,candidates,cached_call or call,target/'story_selection.json')
    if cached_call:
        cached_call.finish()
        assert report['selected']==replay['story_selection.json']['selected']
    selected={(p['start'],p['end']):p for p in report['selected']}
    expected=(76,87)
    result=dict(source_sha256=quality['source_sha256'],transcript_sha256=experiment.digest(cues),
        selection_policy_sha256=hashlib.sha256(Path(experiment.__file__).read_bytes()).hexdigest(),
        candidates_tested=len(candidates),actual_company_qa_selected=expected in selected,
        generic_theory_rejected=(333,360) not in selected,
        future_company_comparison_rejected=(106,123) not in selected,
        selection_completed=report['selection_completed'],diagnostic_only=True,
        media_approved=False,registered_stock=False,published=False,
        selector_evidence_mode='immutable_actual_response_replay' if replay else 'fresh_local_cpu',
        selector_replay_artifact=int(artifact) if artifact else None)
    if expected in selected:
        result['actual_company_selection']=selected[expected]['story_selection']
    # Controlled negative cases, separate from the nine real source clips.
    # No hypothetical or denied host premise may manufacture a past choice.
    negative_texts=[
        '如果将来去投资某家公司，比如茅台，你会怎么选？这只是在讨论假设，并没有发生这样的买入。我们现在不能给出具体公司的投资决定。',
        '您当年买过腾讯，是因为它的游戏业务吗？不是，那个假设不成立，刚才没有提过这笔交易。我们这里只是在举一个将来的例子，不是在讲已经发生的投资。',
        '您当年为什么投资腾讯？这个问题我们先不谈，下一位朋友请提问。今天主要讨论一般性的阅读方法和基础知识。',
        '未来十年，茅台和可口可乐哪家会更好？未来的事需要继续观察，现在很难下结论。我们只是比较可能性，并没有回顾某次实际投资。',
    ]
    negative_cues=[dict(text=text) for text in negative_texts]
    negative_call=RecordedCalls(replay['controlled-negative-selection.json']) if replay else None
    negatives=experiment.choose_stories(negative_cues,[dict(start=i,end=i) for i in range(4)],
        negative_call or call,target/'controlled-negative-selection.json')
    if negative_call:
        negative_call.finish()
        assert negatives['selected']==replay['controlled-negative-selection.json']['selected']
    result['controlled_negative_cases']=len(negative_texts)
    result['hypothetical_denied_unanswered_future_rejected']=(negatives['selection_completed']
        and not negatives['selected'] and len(negatives['rejected'])==len(negative_texts))
    result['passed']=all(result[k] for k in ('actual_company_qa_selected','generic_theory_rejected',
        'future_company_comparison_rejected','selection_completed','hypothetical_denied_unanswered_future_rejected'))
    result['selector_passed']=result['passed']
    (target/'verification.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    assert result['selector_passed'],'Actual complete company Q&A or negative controls failed; not a production approval'
    # The actual selector swapped valid event/detail roles. Check the full
    # production writer against that CURRENT evidence, not a manually supplied
    # title, focus or old copy. This still does not approve source/media.
    item=selected[expected];work=target/'actual-title';work.mkdir(exist_ok=True)
    result['passed']=False
    (target/'verification.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    try:
        copy=production.copywrite(cues,list(range(item['start'],item['end']+1)),
            '林园','访谈','',work,'_actual',story_evidence=item['story_selection'],
            source_sha256=quality['source_sha256'])
        text=''.join(c['text'] for c in cues[item['start']:item['end']+1])
        from title_rewrite import error
        issue=error(copy['title'],copy['title_rewrite'],text)
        focus_issue=experiment.title_focus_error(copy['title'],experiment.title_focus(item['story_selection']),
            cover=copy['cover_title'])
        assert issue is None and focus_issue is None,(issue,focus_issue)
        assert copy['title_rewrite']['review']['method']=='cpu_text_review'
        result.update(actual_title=copy['title'],actual_cover_title=copy['cover_title'],
            actual_title_full_generation_review_passed=True,passed=True)
    except Exception as exc:
        result['actual_title_error']=str(exc)
    (target/'verification.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    print(json.dumps(result,ensure_ascii=False,indent=2),flush=True)
    assert result['passed'],'Actual source-bound company title generation/review failed; not a production approval'


if __name__=='__main__':main()
