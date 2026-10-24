"""Read-only prompt comparison on immutable actual candidates and negative controls.

This does not alter the production prompt, approve media, or register stock.
"""
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import urllib.request
import zipfile

from check_scene_cover_borders import fetch
import publishing_experiment as E


CLARIFICATION = (
    '补充判断口径：本人已经组织或参加的具体活动，或当时公开发言后实际遇到报道并回应，'
    '也可以是亲身经历，不限于股票买卖。仍须已经发生的本人行为或具体事件，'
    '以及另一条实质原话交代经过、直接动机或真实反馈。'
    '只有“我说过”、抽象观点、未来打算、假设都不算。'
    '实际过去行为和后续打算同时出现时，不应抹去过去行为，但事件证据不能选择未来打算。'
    '主持人的假设不能作为嘉宾已发生的经历。保留全部原句，不改写，不跨候选借事实。'
)


def negative_controls():
    # Synthetic text-only cases, explicitly not ASR or source/media evidence.
    cases = [
        ['假如我去年参加了那个会议，我可能会说要注意风险。',
         '媒体也许会报道我说过这句话，如果发生了我会再解释。'],
        ['我打算明年组织很多次活动，我想见五千人。',
         '我希望将来把这些想法讲明白，现在还没有举办这些活动。'],
        ['我们认为长期投资要耐心，不要太在意价格波动。',
         '我们按产品需求判断公司，行业趋势可以作为参考。'],
        ['主持人：林总，如果您当年买入那家公司，会不会一直持有？',
         '我没有做过这个投资，这个假设不能代表我的经历。'],
    ]
    cues=[];picks=[]
    for index, lines in enumerate(cases):
        start=len(cues)
        for number, text in enumerate(lines):
            clock=index*40+number*15
            cues.append(dict(start=clock,end=clock+15,text=text))
        picks.append(dict(start=start,end=len(cues)-1,selection_method='synthetic_negative_control'))
    return cues,picks


def main():
    import produce_cn as P
    assert P.TEXT_BACKEND=='local' and P.LOCAL_LLM_MODEL=='qwen3.5:9b' and P.automatic_only()
    out=Path('personal-story-definition-check');out.mkdir(exist_ok=True)
    result=dict(passed=False,diagnostic_only=True,production_prompt_changed=False,
                registered_stock=False,published=False,media_approved=False)
    def save():(out/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    save()
    with urllib.request.urlopen('http://127.0.0.1:11434/api/tags',timeout=15) as response:
        tags=json.load(response)
    digest=next(m['digest'] for m in tags['models'] if m['name']==P.LOCAL_LLM_MODEL)
    assert digest=='56671c2ab9385f9cfcb404638e32cd62d88e3501d44822208363c010179a3c90'
    result['model_digest']=digest
    artifact=12538697785
    metadata=json.loads(subprocess.check_output(['gh','api',
        f'repos/{os.environ["GITHUB_REPOSITORY"]}/actions/artifacts/{artifact}'],timeout=60))
    assert metadata['workflow_run']['id']==40038403516
    assert metadata['workflow_run']['head_sha']==os.environ['EXPECTED_BASELINE_HEAD']
    with tempfile.TemporaryDirectory(prefix='actual-story-definition-') as tmp:
        archive=Path(tmp)/'source.zip';fetch(artifact,archive)
        with zipfile.ZipFile(archive) as z:
            cues=json.loads(z.read('_tmp/cues_raw.json'))
            source=json.loads(z.read('_tmp/source_quality.json'))
            baseline=json.loads(z.read('_tmp/story_selection.json'))
            cache=json.loads(z.read('_tmp/asr_cache.json'))
    source_sha='e3ad880a98789941fcbb4b9ce52b933297ace03d271ae749f654ffc25ae3c9a3'
    assert source['passed'] is True and source['source_sha256']==source_sha
    assert abs(source['duration_sec']-1499.93)<.01
    assert cache['identity']['source_sha256']==source_sha
    original=E.digest(cues)
    assert original==baseline['transcript_sha256']==cache['cues_sha256']
    assert baseline['selection_completed'] and not baseline['selected'] and not baseline['unresolved']
    candidates=baseline['candidates']
    result.update(original_artifact_id=artifact,source_sha256=source_sha,
                  transcript_sha256=original,baseline_selected=0,
                  baseline_revision=baseline['revision'],candidate_count=len(candidates),
                  clarification=CLARIFICATION)
    (out/'baseline.json').write_text(json.dumps(baseline,ensure_ascii=False,indent=2))
    (out/'original-cues.json').write_text(json.dumps(cues,ensure_ascii=False,indent=2))
    def call(prompt,schema):
        # The saved request contains the actual text sent to the model.
        return P.llm([dict(role='user',content=prompt)],'',temperature=0,max_tokens=512,
                     budget_sec=600,response_schema=schema,read_cache=False)
    old=E.selection_request
    def diagnostic_request(raw,picks):
        prompt,schema=old(raw,picks)
        return CLARIFICATION+'\n'+prompt,schema
    E.selection_request=diagnostic_request
    try:
        actual=E.choose_stories(cues,candidates,call,out/'actual.json',budget_sec=2400)
        result.update(actual_selected=len(actual['selected']),actual_completed=actual['selection_completed'])
        save()
        negatives,negative_picks=negative_controls()
        negative=E.choose_stories(negatives,negative_picks,call,out/'synthetic-negatives.json',budget_sec=1200)
    finally:
        E.selection_request=old
    assert E.digest(cues)==original,'Actual ASR was modified'
    memories=[p for p in candidates if p.get('selection_method')=='literal_memory_reaction_proposal_v1']
    assert len(memories)==1,'Actual complete-memory comparison case changed'
    memory=memories[0]
    selected_memory=any((p['start'],p['end'])==(memory['start'],memory['end']) for p in actual['selected'])
    future=[p for p in candidates if re.search(r'我要抓住这?最后一次机会',
        ''.join(c['text'] for c in cues[p['start']:p['end']+1]))]
    assert len(future)==1,'Actual future-plan control changed'
    future_selected=any((p['start'],p['end'])==(future[0]['start'],future[0]['end']) for p in actual['selected'])
    result.update(complete_memory_selected=selected_memory,future_plan_selected=future_selected,
                  synthetic_controls_completed=negative['selection_completed'],
                  synthetic_false_positives=len(negative['selected']))
    result['passed']=(actual['selection_completed'] and selected_memory and not future_selected
                      and negative['selection_completed'] and not negative['selected'])
    save();print(json.dumps(result,ensure_ascii=False))
    assert result['passed'],'Prompt comparison did not meet positive recall and negative-control requirements'


if __name__=='__main__':main()
