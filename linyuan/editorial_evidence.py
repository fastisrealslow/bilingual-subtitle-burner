"""Bind model evidence to untouched, contiguous source sentences."""
import re


def sentences(text):
    return re.findall(r'[^。！？!?]+[。！？!?]?',text)


def schema(units, claim_excluded=()):
    indices=[-1,*range(len(units))]
    span={'type':'array','items':{'type':'integer','enum':indices},'minItems':2,'maxItems':2}
    allowed=[i for i in range(len(units)) if i not in claim_excluded]
    blocks=[]
    for i in allowed:
        if not blocks or i!=blocks[-1][-1]+1:blocks.append([])
        blocks[-1].append(i)
    claim={'type':'array','items':{'type':'integer','enum':[-1,*allowed]},'minItems':2,'maxItems':2}
    if claim_excluded:
        # Endpoints alone allowed [0,3] across excluded question 1 in actual
        # source752. Both endpoints must now come from ONE uninterrupted
        # nonexcluded block (or the explicit missing-evidence pair). Linear
        # schema size; do not enumerate every O(n^2) possible source span.
        claim['anyOf']=[dict(type='array',items=dict(type='integer',enum=block),
            minItems=2,maxItems=2) for block in [[-1],*blocks]]
    return {
        # Exclusion is not guest attribution or approval. It only prevents a
        # model from putting an explicit host question anywhere in the claim;
        # the bound full range still requires the independent review below.
        'claim_range':claim,
        'reasoning_range':span,'conclusion_range':span,
        'summary':{'type':'string','minLength':1,'maxLength':100},
        'completeness_reason':{'type':'string','minLength':1,'maxLength':250},
        'audio_issues':{'type':'array','maxItems':8,'items':{
            'type':'object','properties':{
                'sentence_id':{'type':'integer','enum':list(range(len(units)))},
                'reason':{'type':'string','minLength':1,'maxLength':100}},
            'required':['sentence_id','reason'],'additionalProperties':False}},
    }


def bind(analysis,units):
    if not isinstance(analysis,dict) or not units:raise ValueError('缺少原文编号证据')
    bound={}
    for kind in ('claim','reasoning','conclusion'):
        span=analysis.get(kind+'_range')
        if not isinstance(span,list) or len(span)!=2 or any(type(i) is not int for i in span):
            raise ValueError('观点证据必须提供起止句编号')
        start,end=span
        if span==[-1,-1]:quote=''
        elif 0<=start<=end<len(units):quote=''.join(units[start:end+1])
        else:raise ValueError('观点证据编号越界或次序错误')
        bound[kind+'_quote']=quote
    for name in ('summary','completeness_reason'):
        value=analysis.get(name)
        if not isinstance(value,str) or not value.strip():raise ValueError('观点审核缺少具体说明')
        bound[name]=value
    issues=analysis.get('audio_issues')
    if not isinstance(issues,list):raise ValueError('缺少识别疑点列表')
    bound['audio_issues']=[]
    for issue in issues:
        if not isinstance(issue,dict):raise ValueError('识别疑点格式错误')
        ident=issue.get('sentence_id');reason=issue.get('reason')
        if type(ident) is not int or not 0<=ident<len(units) or not isinstance(reason,str) or not reason.strip():
            raise ValueError('识别疑点须绑定实际原文编号与理由')
        bound['audio_issues'].append(dict(quote=units[ident],reason=reason))
    # These are observed boundaries supplied for assessment, not generated text.
    bound.update(opening_quote=units[0],ending_quote=units[-1])
    return bound
