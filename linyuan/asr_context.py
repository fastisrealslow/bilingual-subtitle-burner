"""Finite, source-independent recognition hints; never transcript corrections."""
import hashlib
import json
import os
from pathlib import Path


def specification(name=None,root=None):
    name=os.environ.get('QWEN_ASR_CONTEXT_PROFILE','none') if name is None else name
    if name=='none':return dict(text='',identity={})
    if name!='finance-v1':raise ValueError('Unsupported bounded ASR context profile')
    root=Path(root) if root is not None else Path(__file__).resolve().parent
    raw=(root/'asr_finance_vocabulary.json').read_bytes()
    words=json.loads(raw)
    if (not isinstance(words,list) or not 1<=len(words)<=128
            or any(not isinstance(word,str) or not 1<=len(word)<=24
                   or any(ord(c)<32 for c in word) for word in words)):
        raise ValueError('Invalid source-independent recognition vocabulary')
    text='相关词汇：'+'、'.join(words)+'。仅供识别参考，不添加音频中没有的内容。'
    if len(text)>2048:raise ValueError('Recognition vocabulary exceeds bounded context')
    return dict(text=text,identity=dict(profile=name,prompt_version=1,
        text_sha256=hashlib.sha256(text.encode()).hexdigest(),
        vocabulary_sha256=hashlib.sha256(raw).hexdigest()))


def matches(reports,expected=None):
    expected=specification()['identity'] if expected is None else expected
    return all(report.get('recognition_context',{})==expected for report in reports)
