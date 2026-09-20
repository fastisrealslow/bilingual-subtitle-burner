"""Offline Qwen style experiment. Raw model outputs only; no publication APIs."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'linyuan'))
import editorial_policy as ep
from title_rewrite import copy_fragment, relation_error

CASE_INDICES = (0, 1, 2, 3, 4, 5, 6, 7, 10, 11)
FACT_CHECKS = ('source_supported', 'speaker_correct', 'qualifiers_preserved', 'central_point', 'cover_consistent')
VERSION = 'qwen-style-v2'
STYLE_DIMENSIONS = ('voice', 'stance', 'specificity', 'rhythm')


def style_choice(drafts, reviews):
    """Offline style preference, explicitly separate from publication approval."""
    ranked = []
    for i, candidate in enumerate(drafts):
        matches = [r for r in reviews if type(r.get('index')) is int and r['index'] == i]
        if len(matches) != 1:
            continue
        review = matches[0]
        scores = review.get('style_scores', {})
        if (not candidate.get('title', '').startswith('林园：')
                or not 12 <= len(candidate['title']) <= 62
                or copy_fragment(candidate['title'])
                or not all(type(scores.get(k)) is int and 0 <= scores[k] <= 2 for k in STYLE_DIMENSIONS)):
            continue
        ranked.append((sum(scores.values()), i))
    return max(ranked, key=lambda item: (item[0], -item[1]))[1] if ranked else None


def obj(properties):
    return dict(type='object', additionalProperties=False, required=list(properties), properties=properties)


def text():
    return dict(type='string')


def array(item, minimum, maximum):
    return dict(type='array', minItems=minimum, maxItems=maximum, items=item)


def structural_issues(candidate, source):
    title, cover = candidate.get('title', ''), candidate.get('cover', '')
    issues = []
    if not title.startswith('林园：') or not 12 <= len(title) <= 62:
        issues.append('title_prefix_or_length')
    if not 8 <= len(cover) <= 18:
        issues.append('cover_length')
    if copy_fragment(title) or copy_fragment(cover):
        issues.append('fragment')
    if re.search(r'稳赚|保证收益|必涨|不看后悔|震惊|暴富|http|@', title + cover):
        issues.append('unsupported_hype')
    if relation_error(title, cover, source):
        issues.append('relation_reversed')
    for number in re.findall(r'\d+(?:\.\d+)?[%％]?', title + cover):
        if number not in re.findall(r'\d+(?:\.\d+)?[%％]?', source):
            issues.append('new_number')
    return issues


def select(drafts, reviews, source):
    # Missing, duplicate, or malformed reviews cannot silently pass a draft.
    eligible = []
    for i, candidate in enumerate(drafts):
        matches = [r for r in reviews if type(r.get('index')) is int and r['index'] == i]
        if len(matches) != 1:
            continue
        r = matches[0]
        if (all(r.get(k) is True for k in FACT_CHECKS)
                and r.get('natural') is True and not structural_issues(candidate, source)
                and type(r.get('style_fit')) is int and 3 <= r['style_fit'] <= 5
                and len(r.get('a_reason', '')) >= 8):
            eligible.append((r['style_fit'], i))
    return max(eligible, key=lambda x: (x[0], -x[1]))[1] if eligible else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--case', type=int, required=True, choices=range(10))
    args = ap.parse_args()
    model = os.environ.get('LOCAL_LLM_MODEL')
    if model != 'qwen3:8b' or os.environ.get('TEXT_BACKEND') != 'local':
        raise RuntimeError('This trial requires the production local qwen3:8b model')
    corpus = json.loads((ROOT / 'linyuan/simulations/title-batch-20260920/corpus.json').read_text())
    case = corpus[CASE_INDICES[args.case]]
    cues = [c['text'] for c in case['cues']]
    source = ''.join(cues)
    assert ep.text_digest(source) == case['transcript_sha256']
    references = ROOT / 'linyuan/simulations/qwen-style-20260920/reference_titles.json'
    style = json.loads(references.read_text())
    output = ROOT / 'qwen-style-results'
    output.mkdir(exist_ok=True)
    target = output / f'case-{args.case}.json'
    row = dict(version=VERSION, case_number=args.case + 1, case=case['id'],
               old_title=case['old_title'], model=model, seed=20260920,
               commit=os.environ.get('GITHUB_SHA'), run_id=os.environ.get('GITHUB_RUN_ID'),
               transcript_sha256=case['transcript_sha256'], source_run=case['run_id'],
               code_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
               reference_sha256=hashlib.sha256(references.read_bytes()).hexdigest(),
               status='running', calls=[], drafts=[], reviews=[], selected_index=None,
               style_selected_index=None, publication_authorized=False)

    def save():
        target.write_text(json.dumps(row, ensure_ascii=False, indent=2))

    def call(stage, prompt, schema, temperature, tokens):
        body = dict(model=model, messages=[dict(role='user', content=prompt)],
                    stream=False, think=False, format=schema, keep_alive='24h',
                    options=dict(temperature=temperature, seed=20260920, num_ctx=16384, num_predict=tokens))
        record = dict(stage=stage, request=body)
        row['calls'].append(record)
        save()
        start = time.monotonic()
        try:
            request = urllib.request.Request('http://127.0.0.1:11434/api/chat',
                data=json.dumps(body).encode(), headers={'Content-Type': 'application/json'})
            with urllib.request.urlopen(request, timeout=600) as response:
                result = json.load(response)
            record['response'] = result
            if result.get('done_reason') == 'length' or not result.get('done'):
                raise RuntimeError('Incomplete model output; do not use a quote fallback')
            return json.loads(result['message']['content'])
        except Exception as exc:
            record['error'] = f'{type(exc).__name__}: {exc}'
            raise
        finally:
            record['seconds'] = round(time.monotonic() - start, 2)
            save()

    full = json.dumps(dict(enumerate(cues)), ensure_ascii=False)
    try:
        row['model_details'] = json.load(urllib.request.urlopen('http://127.0.0.1:11434/api/tags', timeout=15))
        reading = call('reading', f'''阅读下面真实访谈字幕，只识别林园自己的明确回答，不写标题。
主持人的问题、假设、总结与最后一个尚未得到回答的问题，不能当作林园的主张。
字幕有口吃和识别错误。保留条件、否定、比较对象，不把个案收益当普遍保证。
输出a_guest_claim（最多80字的核心判断）、b_host_premise（最多60字，区分未确认假设）、
c_evidence_ids（支撑核心判断的嘉宾原文编号，连续断句可选多个）。只输出JSON。
完整字幕：{full}''', obj(dict(a_guest_claim=text(), b_host_premise=text(),
            c_evidence_ids=array(dict(type='integer', minimum=0, maximum=len(cues)-1), 1, 16))), 0, 650)
        row['reading'] = reading
        ids = reading['c_evidence_ids']
        if not ids or any(type(i) is not int or not 0 <= i < len(cues) for i in ids):
            raise ValueError('Invalid evidence IDs')
        evidence = {i: cues[i] for i in ids}
        examples = json.dumps([r['title'] for r in style['examples']], ensure_ascii=False)
        draft = call('draft', f'''你为林园真实访谈写B站标题，学习“园园滚雪球”的口吻。
以下样本只用于学习表达节奏，里面的公司、数字、观点不是本片事实，严禁搬入新标题：
{examples}
本轮目标：先对齐园园文风。写成林园本人对着观众讲话，别写成旁观者总结。态度、对象、理由都要具体。
第一句先亮出嘉宾确实表达的选择、判断或感受；第二句接他原话里的具体理由或真实反差。
允许两三句连着说，允许有力的否定和适度重复强调；不要为了书面工整把语气磨平。
强调重复必须带来新意思。例如表态后要解释为什么，不能连续三遍“我不卖”却没有对象和理由。
写“我不投”前必须确认本段确实说了不投；认可前景但说赚钱难，不能硬套成不投。
少用“因为……所以……”的报告句式，直接用日常口吻把原因接上；不必每条有感叹号。
避免“我坚持长期”“坚持投资理念”“核心逻辑”“配置策略”“林园给出理由”等空泛标签；从原文找一个具体做法代替。
保留有辨识度的原话，但删掉“我不会说去卖”“这个那个”这种没有信息的绕口填充。
封面不要重复姓名，用具体对象＋明确判断，不写“林园给出理由”。
仅在嘉宾确实说了自己选择时用“我”；不要每条都写为什么，不要研究报告腔或泛泛总结。
不能凭空添加“不投”“赚大钱”等立场；保留“我”“可能”“如果”等必要限定。
事实只来自本片字幕。下面的阅读摘要只是辅助，可能有误，必须核对原文。
阅读摘要：{json.dumps(reading, ensure_ascii=False)}
初选嘉宾证据：{json.dumps(evidence, ensure_ascii=False)}
完整字幕供核对：{full}
为同一个核心判断写3个不同表达的候选：A直给态度＋理由；B原文真实反差；C具体做法＋理由。
三个候选不能只替换一个词或标点。不要为了满足某种结构凭空制造对立或因果。
title以“林园：”开头，正文22~52字，最多两三个短句，不凑长度，完整自然。cover为8~18字的完整短句，与标题同一判断。
不截断，不抄整段口吃，不把主持人观点改成嘉宾断言。每个候选附2~8个实际嘉宾证据编号。
只输出JSON candidates数组，每项title、cover、evidence_ids。''', obj(dict(candidates=array(obj(dict(
            title=text(), cover=text(), evidence_ids=array(dict(type='integer', minimum=0,
            maximum=len(cues)-1), 2, 8))), 3, 3))), .35, 1150)
        row['drafts'] = draft['candidates']
        if len(row['drafts']) != 3:
            raise ValueError('Expected three Qwen candidates')
        for c in row['drafts']:
            c['structural_issues'] = structural_issues(c, source)
            c['evidence'] = [cues[i] for i in c['evidence_ids']]
        save()
        fields = dict(a_reason=text(), index=dict(type='integer', minimum=0, maximum=2))
        fields.update({k: dict(type='boolean') for k in FACT_CHECKS})
        fields.update(natural=dict(type='boolean'), style_fit=dict(type='integer', minimum=1, maximum=5))
        fields['style_scores'] = obj({k: dict(type='integer', minimum=0, maximum=2) for k in STYLE_DIMENSIONS})
        review = call('review', f'''独立核对3个候选标题与封面。不要修改标题，不相信上游模型选的事实。
逐句阅读完整字幕，区分主持人的问题/假设/总结与嘉宾实际回答，避免把短答当成确认所有提问前提。
source_supported原文支持；speaker_correct观点确属嘉宾；qualifiers_preserved保留否定、条件、范围和不确定性；
central_point有充分内容支持，不是片尾未回答的问题；cover_consistent封面与标题一致且没扩大结论；
natural独立可懂，没有口吃、术语堆砌、主语丢失；style_fit为1~5的园园口吻贴近度：具体对象＋鲜明个人选择或判断＋自然理由。
风格像本人说话，不能以感叹号数量评判；空泛的“理念重要”“核心逻辑”最多2分。风格分不能代替事实检查。
本轮把风格偏好与事实检查分开记录。style_scores四项分别0~2分：
voice：是否像本人自然说话，而非研究报告；stance：是否一开头有明确态度或判断；
specificity：有没有具体对象/理由，避免“坚持长期”空话；rhythm：短句有推进，强调有作用，避免重复三遍同一句。
贴近参考样本的有力语气应保留，不要仅因措辞鲜明而扣风格分；事实偏差单独在对应布尔检查中标明。
风格参考（只看表达方式，不作为本片事实）：{examples}
对每条先用a_reason说明谁说了什么、标题的具体依据或问题，再给布尔结论。必须评完索引0、1、2。
候选：{json.dumps([dict(index=i, title=c['title'], cover=c['cover']) for i,c in enumerate(row['drafts'])], ensure_ascii=False)}
完整原文：{full}
只输出reviews数组。''', obj(dict(reviews=array(obj(fields), 3, 3))), 0, 1400)
        row['reviews'] = review['reviews']
        row['style_selected_index'] = style_choice(row['drafts'], row['reviews'])
        row['selected_index'] = select(row['drafts'], row['reviews'], source)
        row['status'] = 'accepted' if row['selected_index'] is not None else 'rejected'
    except Exception as exc:
        row['status'] = 'unresolved'
        row['error'] = f'{type(exc).__name__}: {exc}'
    save()
    row['inference_summary'] = [dict(stage=c['stage'], seconds=c.get('seconds'),
        model=c.get('response',{}).get('model'), tokens=c.get('response',{}).get('eval_count'),
        done_reason=c.get('response',{}).get('done_reason')) for c in row['calls']]
    save()
    # Keep all failures visible. Job success alone is never a title acceptance.
    print('QWEN_RESULT ' + json.dumps({k:v for k,v in row.items() if k not in ('calls','model_details')}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
