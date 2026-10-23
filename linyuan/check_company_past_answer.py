"""Fresh CPU classifier checks of whole, unchanged actual candidate sets.

Diagnostic only. No selected answer, title, inventory or publication inputs.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import zipfile


def load_actual(artifact):
    with tempfile.TemporaryDirectory() as tmp:
        p=Path(tmp)/'actual.zip'
        with p.open('wb') as f:
            subprocess.run(['gh','api',f'repos/{os.environ["GITHUB_REPOSITORY"]}/actions/artifacts/{artifact}/zip'],
                stdout=f,check=True,timeout=180)
        with zipfile.ZipFile(p) as z:
            cues=json.loads(z.read('_tmp/cues_raw.json'))
            report=json.loads(z.read('_tmp/story_selection.json'))
            source=json.loads(z.read('_tmp/source_quality.json'))
    return cues,report,source


def main():
    import publishing_experiment as E
    import produce_cn as P
    assert P.TEXT_BACKEND=='local' and P.LOCAL_LLM_MODEL=='qwen3.5:9b'
    out=Path('company-past-answer-verification');out.mkdir(exist_ok=True)
    result=dict(passed=False,diagnostic_only=True,registered_stock=False,published=False,
        media_approved=False,selection_policy_sha256=hashlib.sha256(Path(E.__file__).read_bytes()).hexdigest())
    def save(): (out/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    def call(prompt,schema):
        return P.llm([dict(role='user',content=prompt)],'',temperature=0,max_tokens=1600,
            budget_sec=P.text_budget(240),response_schema=schema,read_cache=False)
    save()
    cues,previous,source=load_actual(12509519644)
    assert source['source_sha256']=='6f5ddecc6db4f2045e37a83f63a7d3a122f08287ee1258e9c6f085abdb2b9c2d'
    assert previous['transcript_sha256']==E.digest(cues)
    assert len(previous['candidates'])==9 and previous['selected']==[] and previous['selection_completed']
    actual=E.choose_stories(cues,previous['candidates'],call,out/'actual-all-nine.json')
    selected={(p['start'],p['end']):p for p in actual['selected']}
    result.update(actual_source_sha256=source['source_sha256'],transcript_sha256=E.digest(cues),
        actual_candidates_tested=9,old_selected_count=0,
        new_selected_ranges=[list(k) for k in selected],actual_completed=actual['selection_completed'],
        actual_target_company_selected=(95,105) in selected and selected[(95,105)]['story_selection']['kind']=='company_case',
        actual_other_eight_still_rejected=set(selected)=={(95,105)})
    save()
    # All nine old original candidates, including the already proved company
    # choice, are separate controls. Never supply an acceptance to the model.
    old_cues,old,old_source=load_actual(12428005891)
    assert old_source['source_sha256']==source['source_sha256']
    assert old['transcript_sha256']==E.digest(old_cues) and len(old['candidates'])==9
    controls=E.choose_stories(old_cues,old['candidates'],call,out/'original-all-nine.json')
    control_selected={(p['start'],p['end']):p for p in controls['selected']}
    result.update(original_candidates_tested=9,original_completed=controls['selection_completed'],
        original_company_control_retained=set(control_selected)=={(76,87)}
            and control_selected[(76,87)]['story_selection']['kind']=='company_case')
    save()
    texts=[
        '那未来会继续投资腾讯吗？未来有可能会买，但现在还没买入。我们还需要看看业务，暂时没有实际决定。',
        '那未来会继续持有茅台吗？如果我当年买了并持有二十年，今天就会有这样的收益。但这只是一个假设例子，我没有这笔持仓。',
        '您当年为什么投资腾讯？不是，我们从来没买过腾讯。刚才只是在比较行业，不是在介绍我们的投资经历。',
        '您当年为什么投资腾讯？这个问题我们先不谈，下一位朋友请提问。今天主要讨论一般性的阅读方法和基础知识。',
    ]
    negative=E.choose_stories([dict(text=t) for t in texts],[dict(start=i,end=i) for i in range(len(texts))],
        call,out/'negative-controls.json')
    result.update(negative_cases_tested=len(texts),negative_controls_rejected=negative['selection_completed']
        and not negative['selected'] and len(negative['rejected'])==len(texts))
    result['passed']=all(result[k] for k in ('actual_completed','actual_target_company_selected',
        'actual_other_eight_still_rejected','original_completed','original_company_control_retained',
        'negative_controls_rejected'))
    save();print(json.dumps(result,ensure_ascii=False),flush=True)
    assert result['passed'],'Actual complete source or negative controls failed; no production approval'


if __name__=='__main__':main()
