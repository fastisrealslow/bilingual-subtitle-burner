"""Offline repeated title trial: comparative selection, audit, one bounded repair."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import random
import time
import urllib.request

from qwen_style_trial import CASE_INDICES, ROOT, array, obj, text

VERSION = 'qwen-style-v3'
CHECKS = ('source_supported', 'speaker_correct', 'qualifiers_preserved',
          'natural', 'terminology_clear', 'style_preserved', 'cover_consistent')


def choose(comparison, count=3):
    ranking = comparison.get('ranking', [])
    if (len(ranking) != count or any(type(i) is not int for i in ranking)
            or sorted(ranking) != list(range(count))):
        raise ValueError('Comparison must rank each candidate exactly once')
    reasons = comparison.get('reasons', [])
    if (len(reasons) != count or sorted(r.get('index', -1) for r in reasons) != list(range(count))
            or any(type(r.get('index')) is not int or len(r.get('reason', '')) < 4 for r in reasons)):
        raise ValueError('Every candidate needs its own comparison reason')
    return ranking[0]


def format_issues(draft):
    title, cover = draft.get('title', ''), draft.get('cover', '')
    issues = []
    if not title.startswith('林园：') or not 12 <= len(title) <= 62:
        issues.append('title_prefix_or_length')
    if not 8 <= len(cover) <= 18 or '林园' in cover:
        issues.append('cover_length_or_repeated_name')
    return issues


def audit_ok(audit, draft, cues):
    if not all(audit.get(k) is True for k in CHECKS) or format_issues(draft):
        return False
    if audit.get('issues') != []:
        return False
    evidence = audit.get('evidence', [])
    # Model-supplied evidence must actually occur in the original transcript.
    source = ''.join(cues)
    return bool(evidence) and all(isinstance(q, str) and len(q) >= 4 and q in source for q in evidence)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--case', type=int, required=True, choices=range(10))
    ap.add_argument('--repeat', type=int, required=True, choices=(1, 2, 3))
    args = ap.parse_args()
    if os.environ.get('LOCAL_LLM_MODEL') != 'qwen3:8b' or os.environ.get('TEXT_BACKEND') != 'local':
        raise RuntimeError('Trial requires local qwen3:8b')
    seed = 20260920 + args.repeat - 1
    corpus = json.loads((ROOT / 'linyuan/simulations/title-batch-20260920/corpus.json').read_text())
    case = corpus[CASE_INDICES[args.case]]
    cues = [c['text'] for c in case['cues']]
    source = ''.join(cues)
    # Same digest implementation as the previous experiment.
    import editorial_policy as ep
    assert ep.text_digest(source) == case['transcript_sha256']
    references = ROOT / 'linyuan/simulations/qwen-style-20260920/reference_titles.json'
    examples = json.dumps([r['title'] for r in json.loads(references.read_text())['examples']], ensure_ascii=False)
    full = json.dumps(dict(enumerate(cues)), ensure_ascii=False)
    out = ROOT / 'qwen-style-v3-results'
    out.mkdir(exist_ok=True)
    target = out / f'case-{args.case}-repeat-{args.repeat}.json'
    row = dict(version=VERSION, case_number=args.case+1, repeat=args.repeat, seed=seed,
               case=case['id'], old_title=case['old_title'], model='qwen3:8b',
               commit=os.environ.get('GITHUB_SHA'), run_id=os.environ.get('GITHUB_RUN_ID'),
               transcript_sha256=case['transcript_sha256'],
               reference_sha256=hashlib.sha256(references.read_bytes()).hexdigest(),
               code_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
               publication_authorized=False, status='running', calls=[], drafts=[],
               style_selected_index=None, final_candidate=None, audit=None, repair=None, final_audit=None)

    def save():
        target.write_text(json.dumps(row, ensure_ascii=False, indent=2))

    def call(stage, prompt, schema, temperature=0, tokens=700):
        body = dict(model='qwen3:8b', messages=[dict(role='user', content=prompt)],
                    stream=False, think=False, format=schema, keep_alive='24h',
                    options=dict(temperature=temperature, seed=seed, num_ctx=16384, num_predict=tokens))
        record = dict(stage=stage, request=body)
        row['calls'].append(record)
        save()
        started = time.monotonic()
        try:
            req = urllib.request.Request('http://127.0.0.1:11434/api/chat',
                data=json.dumps(body).encode(), headers={'Content-Type': 'application/json'})
            with urllib.request.urlopen(req, timeout=600) as response:
                result = json.load(response)
            record['response'] = result
            if not result.get('done') or result.get('done_reason') == 'length':
                raise RuntimeError('Incomplete model output')
            return json.loads(result['message']['content'])
        except Exception as exc:
            record['error'] = f'{type(exc).__name__}: {exc}'
            raise
        finally:
            record['seconds'] = round(time.monotonic()-started, 2)
            save()

    candidate_schema = obj(dict(title=text(), cover=text()))
    audit_fields = dict(reason=text(), evidence=array(text(), 1, 4), issues=array(text(), 0, 5))
    audit_fields.update({key: dict(type='boolean') for key in CHECKS})
    audit_schema = obj(audit_fields)

    def audit(candidate, stage):
        return call(stage, f'''核对下面访谈标题与封面，只审查，不改写。先给不超过100字的reason。
检查项：source_supported每个主张原文支持；speaker_correct属于嘉宾而非主持人提问；
qualifiers_preserved保留范围、条件、否定及不确定性；natural没有别扭搭配或空泛重复；
terminology_clear没有明显字幕错词或无法确认的术语；style_preserved保留本人讲话的鲜明态度和口语；
cover_consistent封面与标题同一观点，没有缩小/扩大对象。
明显的否定、有力语气本身不是问题。不要把观点改成报告腔。
原文未说不投就不能补不投，不能凭风格参考添加泡沫、AI收益等事实。
如果字幕有疑似错词，不能原样带到标题，也不能未经原音确认宣称已纠正；可避开不确定术语。
“十年回本”和“十年收回五六成”不是一回事。必须检查标题中具体收益数字对应的条件。
evidence只从原始字幕连续逐字摘录1~4段证据，不改错字，每段最多45字。
issues列出具体问题，无问题时为空数组。任何问题对应检查必须false。
标题封面：{json.dumps(candidate, ensure_ascii=False)}
原始字幕：{full}''', audit_schema, tokens=700)

    try:
        row['model_details'] = json.load(urllib.request.urlopen('http://127.0.0.1:11434/api/tags', timeout=15))
        draft = call('draft', f'''为真实林园访谈写3个不同角度的B站标题及封面，只输出JSON。
风格参考仅用于口吻，其事实绝不能搬入本片：{examples}
保持园园风格：本人直接讲话，先亮态度/判断，再接具体理由；两三句连着说，后一句增加信息。
允许鲜明否定和适度强调，不强求感叹号，不写研究报告。“我”只用于嘉宾自己的立场。
保留有辨识度的原话，去掉口吃填充；避免空泛“坚持长期/核心逻辑/理念”、重复三遍不卖。
不能每条硬套“我不投”。三个候选要在表达或侧重点上有差别，不只换标点。
事实来自下面原始字幕。区分嘉宾回答与主持人提问、假设、总结；未回答的问题不能作结论。
不得新增立场、因果、比较或保证。对疑似识别错词可避开，不能猜新事实或照抄明显错词。
title以“林园：”开头，总长12~62字，自然完整优先，不为凑字数加内容。
cover为8~18字，不含林园姓名，具体对象加判断，与标题同一观点。
原始字幕：{full}''', obj(dict(candidates=array(candidate_schema, 3, 3))), temperature=.35, tokens=800)
        row['drafts'] = draft['candidates']
        if len(row['drafts']) != 3:
            raise ValueError('Expected three candidates')
        presented = [dict(index=i, **c) for i,c in enumerate(row['drafts'])]
        random.Random(seed + args.case).shuffle(presented)
        row['presentation_order'] = [c['index'] for c in presented]
        comparison = call('compare', f'''你是园园风格编辑。直接比较三个候选，给唯一排序，不能并列、不能打分。
目标：自然像本人说话；开头有具体态度；理由/对象清楚；短句推进，有力且不空泛重复。
有力否定不扣分，但别扭搭配、语意不明、同一句反复讲应排后。短不是缺点，信息完整才重要。
参考风格：{examples}
候选显示顺序已打乱，index是永久编号，不是显示位置：{json.dumps(presented, ensure_ascii=False)}
ranking按最好到最差输出全部三个index。reasons每个index给一句具体比较理由，最多45字。
本次只比较风格，事实随后单独核对；不改写任何候选。''', obj(dict(
            ranking=array(dict(type='integer', minimum=0, maximum=2),3,3),
            reasons=array(obj(dict(index=dict(type='integer', minimum=0, maximum=2),reason=text())),3,3))), tokens=600)
        row['comparison'] = comparison
        index = choose(comparison)
        row['style_selected_index'] = index
        chosen = row['drafts'][index]
        row['audit'] = audit(chosen, 'audit')
        if audit_ok(row['audit'], chosen, cues):
            row['final_candidate'] = chosen
            row['status'] = 'accepted_initial'
        else:
            row['repair'] = call('repair', f'''修正这一个标题与封面，最多一轮。保留鲜明个人口吻和短句节奏。
只修列出的问题，不把标题重写成报告腔；无法局部修正时，从同段原文另选有依据的具体判断。
原候选：{json.dumps(chosen, ensure_ascii=False)}
核对问题：{json.dumps(row['audit'], ensure_ascii=False)}
程序格式问题：{json.dumps(format_issues(chosen), ensure_ascii=False)}
疑似字幕错词没有原音确认时，避开不确定术语，不新增事实。不得把主持人的问题当嘉宾结论。
标题以林园：开头，12~62字；封面8~18字，不含林园姓名。changes说明改了什么，最多60字。
原始字幕：{full}''', obj(dict(title=text(), cover=text(), changes=text())), tokens=500)
            fixed = {k:row['repair'][k] for k in ('title','cover')}
            row['final_audit'] = audit(fixed, 'audit_repair')
            if audit_ok(row['final_audit'], fixed, cues):
                row['final_candidate'] = fixed
                row['status'] = 'accepted_repaired'
            else:
                row['status'] = 'rejected'
    except Exception as exc:
        row['status'] = 'unresolved'
        row['error'] = f'{type(exc).__name__}: {exc}'
    row['inference_summary'] = [dict(stage=c['stage'], seconds=c.get('seconds'),
        tokens=c.get('response',{}).get('eval_count'), done_reason=c.get('response',{}).get('done_reason'))
        for c in row['calls']]
    save()
    print('QWEN_V3_RESULT ' + json.dumps({k:v for k,v in row.items() if k not in ('calls','model_details')}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
