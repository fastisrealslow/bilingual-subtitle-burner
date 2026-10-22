"""Same nine actual candidates, current real CPU selector, no media approval."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import zipfile


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
    def call(prompt,schema):
        return production.llm([dict(role='user',content=prompt)],'',temperature=0,
            max_tokens=1600,budget_sec=production.text_budget(240),response_schema=schema,read_cache=False)
    report=experiment.choose_stories(cues,candidates,call,target/'story_selection.json')
    selected={(p['start'],p['end']):p for p in report['selected']}
    expected=(76,87)
    result=dict(source_sha256=quality['source_sha256'],transcript_sha256=experiment.digest(cues),
        selection_policy_sha256=hashlib.sha256(Path(experiment.__file__).read_bytes()).hexdigest(),
        candidates_tested=len(candidates),actual_company_qa_selected=expected in selected,
        generic_theory_rejected=(333,360) not in selected,
        future_company_comparison_rejected=(106,123) not in selected,
        selection_completed=report['selection_completed'],diagnostic_only=True,
        media_approved=False,registered_stock=False,published=False)
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
    negatives=experiment.choose_stories(negative_cues,[dict(start=i,end=i) for i in range(4)],
        call,target/'controlled-negative-selection.json')
    result['controlled_negative_cases']=len(negative_texts)
    result['hypothetical_denied_unanswered_future_rejected']=(negatives['selection_completed']
        and not negatives['selected'] and len(negatives['rejected'])==len(negative_texts))
    result['passed']=all(result[k] for k in ('actual_company_qa_selected','generic_theory_rejected',
        'future_company_comparison_rejected','selection_completed','hypothetical_denied_unanswered_future_rejected'))
    (target/'verification.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    print(json.dumps(result,ensure_ascii=False,indent=2),flush=True)
    assert result['passed'],'Actual complete company Q&A or negative controls failed; not a production approval'


if __name__=='__main__':main()
