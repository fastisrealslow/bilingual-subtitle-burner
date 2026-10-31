"""Keep explicit possibility on a literally reused existence claim.

This bounded check does not infer synonyms or certify arbitrary paraphrases.
Title and cover are checked separately; another clause's hedge is not evidence.
"""
import re


def error(title, cover, source):
    sentences = re.split(r'[。！？!?；;\n]', source or '')
    clauses = [c for sentence in sentences for c in re.split(r'[，,：:]', sentence)]
    hedge = r'可能|或许|也许|未必|不一定|如果|假如|假设|要是|一旦|是否|能否|会不会'
    for copy in (title, cover):
        for clause in re.split(r'[，,。！？!?；;：:\n]', copy or ''):
            if re.search(hedge + r'|没有|并非|不是|不见得', clause):
                continue
            claim = re.fullmatch(r'([\u4e00-\u9fff]{2,16}?)(?:已经)?有([\u4e00-\u9fff]{2,16})', clause.strip())
            if not claim:
                continue
            subject, obj = claim.groups()
            # Permit the ordinary noun contraction X有替代(产品), but not
            # arbitrary prefix matches such as 风险 versus 风险投资.
            tail = r'(?:产品)?' if obj.endswith('替代') else ''
            end = r'[啊呀呢吧哦\s]*$'
            uncertain = re.compile(re.escape(subject) + r'(?:也|还)?(?:可能|或许|也许)有' + re.escape(obj) + tail + end)
            if not any(uncertain.search(c.strip()) for c in clauses):
                continue
            affirmative = re.compile(re.escape(subject) + r'(?:已经|确实|本来|也|还)?有' + re.escape(obj) + tail + end)
            independent = False
            for sentence in sentences:
                prefix = ''
                for c in re.split(r'[，,：:]', sentence):
                    if (affirmative.search(c.strip())
                            and not re.search(hedge + r'|不能说|不是说|并非|没有|不认为', c)
                            and not re.search(r'如果|假如|假设|要是|一旦', prefix)):
                        independent = True
                    prefix += c
            if not independent:
                return '原文只说同一对象可能存在；标题和封面须各自保留可能性，不能把可能有写成确定有'
    return None
