"""Bind model evidence to untouched, contiguous source sentences."""
import re


def sentences(text):
    return re.findall(r'[^。！？!?]+[。！？!?]?',text)


def schema(units, claim_excluded=()):
    indices=[-1,*range(len(units))]
    span={'type':'array','items':{'type':'integer','enum':indices},'minItems':2,'maxItems':2}
    return {
        # Exclusion is not guest attribution or approval. It only prevents a
        # model from picking an explicit host question as either claim endpoint;
        # the bound full range still requires the independent review below.
        'claim_range':{'type':'array','items':{'type':'integer',
            'enum':[i for i in indices if i not in claim_excluded]},'minItems':2,'maxItems':2},
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
