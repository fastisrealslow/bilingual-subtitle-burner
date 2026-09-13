"""Source-grounded claim titles: propose angles, review them, never list keywords."""
import difflib
import hashlib
import json
import re

VERSION = 2026091303
CHECKS = ('source_supported', 'central_point', 'attribution_correct',
          'preserves_qualifiers', 'cover_consistent', 'readable')


def compact(text):
    return re.sub(r'[^0-9A-Za-z\u4e00-\u9fff%％.]', '', str(text or ''))


def summary_heading(title):
    body = re.sub(r'^[^：:]+[：:]', '', str(title or ''))
    return bool(re.match(r'(?:谈|浅谈|关于|聊聊|解读|漫谈).{0,50}(?:与|和|、|讨论|投资|需求|时机)', body)
                or re.search(r'公开讨论$|投资(?:逻辑|哲学|理念)与|机遇与挑战', body))


def _json(text):
    match = re.search(r'\{.*\}', text, re.S)
    return json.loads(match.group(0) if match else text)


def source_units(transcript):
    """Number exact source spans; the model selects evidence instead of retyping it."""
    spans = re.findall(r'.+?(?:[。！？!?；;\n]|$)', transcript, re.S)
    units, current = [], ''
    for span in spans:
        for start in range(0, len(span), 100):
            chunk = span[start:start + 100]
            if current and len(current) + len(chunk) > 120:
                units.append(current)
                current = ''
            current += chunk
            if len(compact(current)) >= 30:
                units.append(current)
                current = ''
    if current:
        if units and len(compact(current)) < 8:
            units[-1] += current
        else:
            units.append(current)
    if ''.join(units) != transcript:
        raise ValueError('原文证据分组改变了字幕')
    return units


def subject_catalog(units):
    """Offer exact source nouns, never model-invented compound evidence anchors."""
    from collections import Counter
    import jieba.posseg
    counts = Counter(word for word, tag in jieba.posseg.cut(''.join(units))
                     if tag.startswith(('n', 'vn')) and 2 <= len(compact(word)) <= 8)
    return {word:[i for i, unit in enumerate(units) if word in unit]
            for word, _ in counts.most_common(48)}


def proposal_schema(unit_count, subjects=None):
    # Evidence comes first in the decoding grammar, before any proposed wording.
    fields = {'subject':dict(type='string', **({'enum':list(subjects)} if subjects else {}))}
    fields['evidence_ids'] = dict(type='array', minItems=1, maxItems=4, uniqueItems=True,
        items=dict(type='integer', minimum=0, maximum=max(0, unit_count - 1)))
    fields['claim'] = dict(type='string', minLength=22, maxLength=160)
    copies=dict(title=dict(type='string',minLength=22,maxLength=68),
                cover_title=dict(type='string',minLength=10,maxLength=22))
    return dict(type='object', additionalProperties=False, required=['focus','candidates'], properties={
        'focus':dict(type='object',additionalProperties=False,required=list(fields),properties=fields),
        'candidates':dict(type='array', minItems=3, maxItems=3,
            items=dict(type='object', additionalProperties=False, required=list(copies), properties=copies))})


def review_schema(candidate_count):
    fields = {'reason':dict(type='string', minLength=12, maxLength=160)}
    fields.update({name:dict(type='boolean') for name in CHECKS})
    fields.update(index=dict(type='integer', minimum=0, maximum=candidate_count - 1),
                  appeal=dict(type='integer', minimum=1, maximum=5))
    return dict(type='object', additionalProperties=False, required=['reviews'], properties={
        'reviews':dict(type='array', minItems=candidate_count, maxItems=candidate_count,
            items=dict(type='object', additionalProperties=False, required=list(fields), properties=fields))})


def bind_evidence(item, units):
    if not isinstance(item, dict):
        raise ValueError('候选不是JSON对象')
    ids = item.get('evidence_ids')
    if (not isinstance(ids, list) or not 1 <= len(ids) <= 4 or len(set(ids)) != len(ids)
            or any(type(i) is not int or not 0 <= i < len(units) for i in ids)):
        raise ValueError('原文证据编号无效')
    if 'evidence' in item:
        raise ValueError('证据必须从编号取回，不能由模型另写原文')
    return {**item, 'evidence':[units[i] for i in sorted(ids)]}


def _candidate_error(item, transcript, speaker, existing_titles, check_layout=True):
    title, cover = item.get('title'), item.get('cover_title')
    if not isinstance(title, str) or not title.startswith(speaker + '：'):
        return '标题缺少主讲人前缀'
    if summary_heading(title):
        return '标题必须呈现一个具体观点，不能是主题目录或残句'
    if not 12 <= len(compact(title)) <= 62:
        return f'标题有效字数为{len(compact(title))}，须12~62字；补全具体判断或问题，不用空格凑长度'
    if not isinstance(cover, str) or not 8 <= len(compact(cover)) <= 18:
        return '封面短标题须以完整词句排入两行'
    if check_layout:
        from headline_policy import cover_fits
        if not cover_fits(cover):
            return '封面短标题不能截断词语'
    if re.search(r'必涨|稳赚|翻倍秘籍|震惊|暴富|内幕曝光|不看后悔|http|@', title + cover):
        return '标题含收益诱导或空洞夸张'
    if any(difflib.SequenceMatcher(None, compact(title), compact(old)).ratio() >= .84 for old in existing_titles):
        return '标题与已有稿件重复'
    evidence = item.get('evidence')
    if not isinstance(evidence, list) or not 1 <= len(evidence) <= 4:
        return '必须提供支撑观点的完整原文句子'
    source = compact(transcript)
    if any(not isinstance(q, str) or len(compact(q)) < 8 or compact(q) not in source for q in evidence):
        return '观点证据不是这段真实原文'
    # An observed 4B-model false positive inferred "更安全" from position sizing.
    # Such financial claims need explicit evidence even if a reviewer says true.
    risk_claims=('更安全','更稳妥','更稳健','风险更低','风险小','降低风险','避险',
                 '收益更高','回报更高','更赚钱','最赚钱','稳赚','保证收益')
    stated=compact(''.join(evidence))
    if any(term in compact(title+cover) and term not in stated for term in risk_claims):
        return '标题新增了原文没有的安全性或收益比较结论'
    subject = item.get('subject')
    if not isinstance(subject, str) or not 2 <= len(compact(subject)) <= 12:
        return '缺少具体讨论对象'
    if compact(subject) not in compact(title) or not any(compact(subject) in compact(q) for q in evidence):
        return '标题对象与原文证据不对应'
    # The semantic review also checks written numbers, attribution and negation.
    for number in re.findall(r'\d+(?:\.\d+)?[%％]?', title + cover):
        if number not in re.findall(r'\d+(?:\.\d+)?[%％]?', transcript):
            return '标题或封面添加了原文没有的数字'
    return None


def _binding(title, cover, subject, evidence):
    return hashlib.sha256(json.dumps([title, cover, subject, evidence], ensure_ascii=False).encode()).hexdigest()


def _package(item, transcript, review, candidates):
    title, cover = item['title'], item['cover_title']
    review = {**review, 'copy_sha256':_binding(title, cover, item['subject'], item['evidence'])}
    proof = dict(version=VERSION, kind='editorial_claim', title=title, cover=cover,
                 subject=item['subject'], evidence=item['evidence'], review=review,
                 source_sha256=hashlib.sha256(compact(transcript).encode()).hexdigest())
    return dict(title=title, cover_title=cover, title_rewrite=proof,
                title_candidates=[c['title'] for c in candidates], title_quality_verified=True,
                packaging_method='source_claim_editor', desc='本段讨论：' + title.split('：', 1)[1])


def _extractive(transcript, speaker, existing_titles, preferred=None):
    from headline_policy import title_candidates, body, cover_copy, complete
    titles = title_candidates(transcript, speaker, existing_titles)
    if preferred and preferred not in titles:
        titles.append(preferred)
    for title in titles:
        quote = body(title, speaker)
        if not complete(quote) or compact(quote) not in compact(transcript) or summary_heading(title):
            continue
        cover = cover_copy(title, transcript, speaker)
        if cover.get('reason') == 'needs_editorial_copy':
            continue
        # Whole source claims are the fallback; not a list of detected subjects.
        item = dict(title=title, cover_title=cover['text'], evidence=[quote], subject=quote)
        if not 12 <= len(compact(title)) <= 62:
            continue
        return _package(item, transcript, dict(method='source_quote', quote=quote), [item])
    raise ValueError('未提炼出有原文支撑的完整观点标题；保留素材和转写，等待标题重试')


def generate(transcript, speaker='林园', existing_titles=(), model=None, preferred=None,
             structured_model=None):
    if model is None and structured_model is None:
        return _extractive(transcript, speaker, existing_titles, preferred)
    units = source_units(transcript)
    subjects = subject_catalog(units) if structured_model else {}
    def call(prompt, schema):
        return structured_model(prompt, schema) if structured_model else model(prompt)
    prompt = f'''你是视频标题编辑。读完真实字幕，先找这段最主要的一个观点和它的理由。
不要按出现的关键词凑标题，不要照抄口头残句；不要把主持人的追问当成嘉宾断言。
为同一个中心观点写3个不同角度的标题：具体判断、原文支持的反常识选择、视频确实回答的问题。
不必硬造冲突或数字。允许自然改写和设问；保留否定、条件、可能性，不写保证收益。
不能把控制仓位、提前布局等行为推断成“更安全”“风险更低”“更赚钱”；原文没说就不能加。
每条以“{speaker}：”开头，正文18~36字。封面8~18字，两行能完整读完，表达同一个看点；封面不加姓名前缀。
拒绝“谈A、B与C”“关于某某的公开讨论”“投资逻辑解析”等目录式标题。
先填写focus：从对象表选一个subject原词，用evidence_ids选1~4组证据，在claim写清嘉宾的具体判断、做法及限定条件。
然后围绕这同一个判断写三个不同角度的标题，三条都使用focus的对象和证据，不能各挑不同话题。
程序会取回真实证据原句，不要重新抄写或改写证据。claim是写作草稿，不能代替真实字幕。
subject必须逐字出现在标题与至少一组所选证据中，不能自行组合成新词；这只是证据索引，不是标题模板。
用日常说话表达看点，不写“潜力池”“收益逻辑”“策略解析”等空话。
标题要让没看过采访的人一遍看懂。用具体的选择、疑问或判断吸引观众，不堆“赛道、控比例、潜力”等词。
主体关系不能写反：是行业尚未出现龙头，不是“一家公司未出龙头”；原文说“不如预期但没那么坏”，不能升级成“稳健复苏”。
文风示例（不可套用事实）：原文说“利润涨了但货款收不回，暂时不买”，可写“利润在增长，为什么还要先看回款？”，不能写“回款利润投资逻辑”。
封面必须有8~18个汉字，不能只有一个短词。
旧标题仅供识别问题，不是已验收结论：{preferred or '无'}
只输出符合给定结构的JSON，必须填满3条真实候选，不能返回空字符串。
对象表（原词：它出现的证据编号）：{json.dumps(subjects, ensure_ascii=False)}
真实字幕：\n{transcript}
证据编号（每组均为未改写的原文）：\n{json.dumps(dict(enumerate(units)), ensure_ascii=False)}'''
    last_error = ''
    for attempt in range(3):
        try:
            proposal = _json(call(prompt + ('\n上次问题：' + last_error if last_error else ''),
                                  proposal_schema(len(units), subjects)))
            candidates = proposal.get('candidates')
            if not isinstance(candidates, list) or len(candidates) != 3:
                raise ValueError('必须提供三个不同角度的候选标题')
            # A structured production call never accepts model-authored evidence.
            if structured_model:
                focus=proposal.get('focus') or {}
                if not isinstance(focus.get('claim'),str) or len(compact(focus['claim']))<12:
                    raise ValueError('先写清有原文依据的中心判断和限定条件，再写标题')
                if focus.get('subject') not in subjects:
                    raise ValueError('中心对象必须从原词表选取')
                candidates = [bind_evidence(dict(title=c.get('title'),cover_title=c.get('cover_title'),
                    subject=focus['subject'],evidence_ids=focus.get('evidence_ids')),units) for c in candidates]
            errors = [(_candidate_error(c, transcript, speaker, existing_titles)
                       if isinstance(c, dict) else '候选不是JSON对象') for c in candidates]
            valid = [c for c, issue in zip(candidates, errors) if not issue]
            if len(valid) != 3:
                issues=[f"{c.get('title','')} / {c.get('cover_title','')}"
                        f"（对象={c.get('subject')},证据编号={c.get('evidence_ids')}）：{issue}"
                        for c,issue in zip(candidates,errors) if issue]
                raise ValueError('三个角度均须合格再比较；需修正：' + '；'.join(issues))
            if len({compact(c['title']) for c in valid})!=3:
                raise ValueError('三个标题必须有不同看点，不能重复同一句话')
            judge = f'''独立核对这些视频标题与完整字幕，只评价标题和封面，不重做选段或字幕审核。
先在reason用一句话指出原文依据或标题增加的结论，再给判定。不能空填全部true。
严格寻找标题/封面新增的比较、因果、收益和安全性判断：控制仓位不等于“更安全”，
看好某行业不等于“必然上涨”；原文没有明确支持的新增判断必须source_supported=false。
“没有预期的好但没那么坏”不等于“复苏稳健”；“行业还没有龙头”不等于“未出龙头的公司”。
拗口的术语堆砌、主体关系错误、把有限的肯定扩大成整体乐观，分别判readable、source_supported、preserves_qualifiers=false。
逐条检查：source_supported原文支持；central_point抓住中心而不是举例或旁枝；
attribution_correct没有把主持人的猜测归为嘉宾断言；preserves_qualifiers保留条件否定和不确定性；
cover_consistent封面和标题同一观点且没有更强断言；readable自然好懂。
appeal按具体看点和想点开的程度评1~5，空泛目录只能1分。严格输出布尔值，不因文字流畅而放过编造。
返回JSON：{{"reviews":[{{"reason":"指出原文依据或新增判断，至少12字","index":0,"source_supported":true,"central_point":true,
"attribution_correct":true,"preserves_qualifiers":true,"cover_consistent":true,"readable":true,"appeal":4}}]}}
候选：{json.dumps(valid, ensure_ascii=False)}\n完整字幕：{transcript}'''
            reviews = _json(call(judge, review_schema(len(valid)))).get('reviews', [])
            accepted = []
            for row in reviews:
                if (isinstance(row, dict) and type(row.get('index')) is int and 0 <= row['index'] < len(valid)
                        and all(row.get(k) is True for k in CHECKS)
                        and isinstance(row.get('reason'),str) and len(compact(row['reason'])) >= 12
                        and type(row.get('appeal')) is int and 3 <= row['appeal'] <= 5):
                    accepted.append(row)
            if not accepted:
                raise ValueError('候选没有通过标题原文核验和可读性检查')
            winner = max(accepted, key=lambda r:r['appeal'])
            item = valid[winner['index']]
            return _package(item, transcript, dict(method='cpu_text_review', **winner), valid)
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError) as exc:
            last_error = str(exc)
            print(f'[标题观点] 第{attempt + 1}次生成待修正：{last_error}', flush=True)
    return _extractive(transcript, speaker, existing_titles, preferred)


def error(title, proof, transcript=None, speaker='林园'):
    if (not isinstance(proof, dict) or proof.get('version') != VERSION
            or proof.get('kind') != 'editorial_claim' or summary_heading(title)):
        return '标题需重新提炼具体观点，不能使用旧的关键词拼盘'
    if title != proof.get('title') or not isinstance(proof.get('cover'), str):
        return '标题或封面与观点证明不一致'
    evidence = proof.get('evidence')
    if not isinstance(evidence, list) or not evidence or any(not isinstance(q, str) for q in evidence):
        return '标题缺少完整原文证据'
    if transcript is not None and any(compact(q) not in compact(transcript) for q in evidence):
        return '标题观点不能回溯真实字幕'
    review = proof.get('review') or {}
    if review.get('copy_sha256') != _binding(title, proof['cover'], proof.get('subject'), evidence):
        return '核验后标题、封面或证据发生变化'
    if review.get('method') == 'source_quote':
        from headline_policy import body, complete
        quote = review.get('quote') or ''
        if (body(title, speaker) != quote or evidence != [quote] or not complete(quote)):
            return '原话标题的观点或限定条件已变化'
    elif review.get('method') == 'cpu_text_review':
        if not all(review.get(k) is True for k in CHECKS) or type(review.get('appeal')) is not int or not 3 <= review['appeal'] <= 5:
            return '标题尚未通过独立原文核验'
        if not isinstance(review.get('reason'),str) or len(compact(review['reason'])) < 12:
            return '标题审核缺少原文依据说明'
        item = dict(title=title, cover_title=proof['cover'], subject=proof.get('subject'), evidence=evidence)
        issue = _candidate_error(item, transcript or ''.join(evidence), speaker, (), check_layout=False)
        if issue:
            return issue
    else:
        return '标题审核方法缺失'
    return None
