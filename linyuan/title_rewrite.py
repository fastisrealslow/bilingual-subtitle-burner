"""Readable topic titles when verbatim speech cannot stand alone as a headline.

This fallback names verified subjects; it adds no forecast, quotation or return
claim. A failed quote triggers new copy, rather than discarding usable footage.
"""
import hashlib
import difflib
import re

VERSION = 2026091301
TOPICS = (
    ('人口变化', ('人口',)), ('消费需求', ('消费',)), ('实业经营', ('实业',)),
    ('医药投资', ('医药','药品','制药')), ('老龄化', ('老龄化',)),
    ('企业分红', ('分红',)), ('股息回报', ('股息',)), ('白酒消费', ('白酒',)),
    ('茅台', ('茅台',)), ('片仔癀', ('片仔癀',)), ('现金流', ('现金流',)),
    ('企业盈利', ('盈利','利润','赚钱')), ('科技投资', ('科技',)),
    ('机器人', ('机器人',)), ('持有策略', ('持有',)),
    ('买入时机', ('买入','择时')), ('股票估值', ('估值',)),
    ('企业经营', ('企业',)), ('股票投资', ('股票','投资')),
)


def joined(labels):
    return labels[0] if len(labels)==1 else '、'.join(labels[:-1])+'与'+labels[-1]


def generate(transcript, speaker='林园', existing_titles=()):
    counts = [(label, sum(transcript.count(a) for a in anchors), i)
              for i,(label,anchors) in enumerate(TOPICS)]
    choices = [r for r in counts if r[1]]
    # Generic investing labels must not crowd out specific subjects.
    specific = [r for r in choices if r[2] < 17]
    pool = specific if len(specific)>=2 else choices
    chosen = sorted(sorted(pool,key=lambda r:(-r[1],r[2]))[:3],key=lambda r:r[2])
    if len(chosen)<2:
        raise ValueError('标题重写缺少两个可追溯的具体主题，须重新提炼选段')
    labels=[r[0] for r in chosen]
    compact=lambda s:re.sub(r'[^0-9A-Za-z\u4e00-\u9fff]','',s)
    options=[f'{speaker}：谈'+joined(labels),f'{speaker}：关于'+joined(labels)+'的公开讨论']
    title=next((t for t in options if 12<=len(compact(t))<=62
                and not any(difflib.SequenceMatcher(None,compact(t),compact(old)).ratio()>=.84 for old in existing_titles)),None)
    if not title:
        raise ValueError('主题标题与已发布稿件重复，须选择新的具体角度')
    proof=dict(version=VERSION,kind='editorial_topic',labels=labels,
        title=title,cover=joined(labels[:2]),
        source_sha256=hashlib.sha256(transcript.encode()).hexdigest(),
        evidence={label:[a for a in dict(TOPICS)[label] if a in transcript] for label in labels})
    return dict(title=title,title_rewrite=proof,title_quality_verified=True,
                packaging_method='automatic_topic_rewrite')


def error(title, proof, transcript=None, speaker='林园'):
    if not isinstance(proof,dict) or proof.get('version')!=VERSION or proof.get('kind')!='editorial_topic':
        return '标题重写证明缺失或过期'
    labels=proof.get('labels') or []
    if not 2<=len(labels)<=3 or len(set(labels))!=len(labels) or any(x not in dict(TOPICS) for x in labels):
        return '标题重写主题不在来源词表'
    valid={f'{speaker}：谈'+joined(labels),f'{speaker}：关于'+joined(labels)+'的公开讨论'}
    if title not in valid or proof.get('title')!=title or proof.get('cover')!=joined(labels[:2]):
        return '标题或封面与重写主题不一致'
    for label in labels:
        anchors=(proof.get('evidence') or {}).get(label) or []
        if not anchors or any(a not in dict(TOPICS)[label] for a in anchors):
            return '标题主题缺少原文证据'
        if transcript is not None and not any(a in transcript for a in anchors):
            return '标题主题没有出现在真实字幕中：'+label
    return None
