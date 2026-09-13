"""Exhaustive, non-contradictory judgments for real source ranges."""
import json
import hashlib

VERSION = 4
# Run 798 exhausted the 600-second CPU budget with batches of up to 48 ranges and
# the full transcript. Smaller batches retain exhaustive coverage and leave
# room for the topic map and verdicts without weakening the 120-second rule.
BATCH_SIZE = 16
ACCEPT = {f'accept_{score}': score for score in range(7, 11)}
REJECT = {
    'reject_opening': '开头缺必要上下文',
    'reject_ending': '结尾未自然结束',
    'reject_mixed_topics': '拼接了不同话题',
    'reject_explanation': '缺少观点的必要解释',
    'reject_low_value': '不足7分，不适合独立成片',
}


def sentence_starts(cues):
    """Use the same exact cue boundaries for model decoding and validation."""
    import re
    return [0] + [i+1 for i, cue in enumerate(cues[:-1])
                  if re.search(r'[。！？!?][”’」』\"]?\s*$', cue['text'])]


def schema(choices, cue_count, cues=None):
    fields = {str(c['candidate_id']): {'type': 'string', 'enum': list(ACCEPT)+list(REJECT)}
              for c in choices}
    start_rule = ({'type':'integer','enum':sentence_starts(cues)} if cues is not None
                  else {'type':'integer','minimum':0,'maximum':cue_count-1})
    topic_fields={'start':start_rule,
                  'topic':{'type':'string','minLength':1,'maxLength':50}}
    return {'type': 'object', 'properties': {
        'topics':{'type':'array','minItems':1,'maxItems':cue_count,'items':{
            'type':'object','properties':topic_fields,'required':list(topic_fields),
            'additionalProperties':False}}, 'verdicts': {
        'type': 'object', 'properties': fields, 'required': list(fields),
        'additionalProperties': False}}, 'required': ['topics','verdicts'], 'additionalProperties': False}


def prompt(transcript, choices, speaker):
    return (f'你是{speaker}访谈编辑。先划分完整原文的话题，再逐项审核候选。'
        'topics只列主要话题的起点start和简短名称topic，第一项start必须为0，后续严格递增。每个话题延续到下一个起点之前，最后一个延续到原文末尾，结束编号由程序计算。'
        '同一问题下的观点、理由、例子、反问和总结必须归为同一话题，不能逐句划分。每一项是一个可独立说明的主题，不得把所有财经讨论都笼统称为投资。'
        '相关的理由、例子和同主题追问属于一个主题；转问另一种投资逻辑或另一件事必须另起主题。'
        '在完整句边界换题，主持人的引入和提问属于后面回答的主题。'
        '不要为了让候选满足120秒而合并不同话题。topic用中文简述这段原话具体讨论什么。'
        '一个候选只能在一个主题内部；跨越两个主题的候选必须判为reject_mixed_topics。'
        '每个候选的真实时长已由程序确认至少120秒；时长足够不代表内容合格。'
        '必须根据候选起止字幕之间的全部原话判断：开头独立可懂，一个完整主题，'
        '必要解释充分，结尾自然收束，并有至少7分的独立观看价值。'
        '同主题追问可以保留；不同问题拼接、寒暄、下一话题未说完都不能用来凑时长。'
        '不得猜测原片之外的解释，不得改写、补字或修改候选边界。'
        '每个候选只填一个判定：合格为accept_7、accept_8、accept_9或accept_10；'
        '不合格必须选reject_opening、reject_ending、reject_mixed_topics、'
        'reject_explanation或reject_low_value。不能只评价头两个候选。'
        '输出JSON对象含topics和verdicts。topics每项start,topic；verdicts的键是本批每个候选ID，'
        '值是上述判定；所有ID必须恰好出现一次。'
        '\n完整原文：\n'+transcript+'\n本批候选：\n'+json.dumps(choices, ensure_ascii=False))


def topic_schema(cues):
    result=schema([],len(cues),cues)
    result['properties'].pop('verdicts')
    result['required']=['topics']
    return result


def topic_prompt(transcript,cues,speaker):
    return (f'你是{speaker}访谈编辑。只给完整原文划分主要话题，不逐句概括。'
        '同一问题下的观点、解释、案例、交易细节、反问和总结属于同一话题；'
        '同义名称、同一事件的细节或叙述进展不能单独另起话题。'
        '主持人转问另一个独立问题或发言转向另一种投资逻辑时才换题。'
        '不得为了达到时长要求合并无关问题；也不得把一个连续故事拆成逐句目录。'
        '每项只写起点start及不超过20字的主题topic；第一项必须从0开始，后续严格递增。'
        '完整原句不能切断。只输出JSON对象topics，不写候选判定。'
        '\n可用完整句起点start：'+json.dumps(sentence_starts(cues))+
        '\n完整原文：\n'+transcript)


def parse_topics(answer,cues):
    data=json.loads(answer)
    if not isinstance(data,dict) or set(data)!={'topics'}:
        raise ValueError('完整话题图格式无效')
    _,topics,_=parse(json.dumps(dict(topics=data['topics'],verdicts={})),[],cues)
    if any(a['topic'].strip()==b['topic'].strip() for a,b in zip(topics,topics[1:])):
        raise ValueError('相邻同一主题被重复拆分，不能据此淘汰素材')
    return topics


def verdict_schema(choices):
    result=schema(choices,1)
    result['properties'].pop('topics')
    result['required']=['verdicts']
    return result


def verdict_prompt(transcript,choices,topics,speaker):
    return (f'你是{speaker}访谈编辑。以下话题图已确定，禁止重新切分或改写它。'
        '逐项检查候选是否独立可懂、解释充分、结尾自然、有至少7分观看价值。'
        '一个问题下的理由和例子不是混杂话题。每个候选只能给一个判定。'
        '合格用accept_7到accept_10；不合格用'+','.join(REJECT)+
        '。所有候选ID必须恰好出现一次。只输出JSON对象verdicts，不重复输出话题图。'
        '\n话题图：'+json.dumps(topics,ensure_ascii=False)+
        '\n完整原文：\n'+transcript+'\n本批候选：'+json.dumps(choices,ensure_ascii=False))


def parse_verdicts(answer,choices,topics,cues):
    data=json.loads(answer,object_pairs_hook=unique_object)
    if not isinstance(data,dict) or set(data)!={'verdicts'}:
        raise ValueError('逐项判定格式无效')
    compact=[{k:v for k,v in t.items() if k!='end'} for t in topics]
    return parse(json.dumps(dict(topics=compact,verdicts=data['verdicts'])),choices,cues)[0]


def unique_object(pairs):
    result={}
    for key,value in pairs:
        if key in result:raise ValueError('逐项审查包含重复候选ID或字段')
        result[key]=value
    return result


def parse(answer, choices, cues):
    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('逐项审查包含重复候选ID或字段')
            result[key] = value
        return result
    data = json.loads(answer, object_pairs_hook=unique_object)
    if not isinstance(data, dict) or set(data) != {'topics','verdicts'}:
        raise ValueError('逐项审查缺少话题范围或唯一的verdicts对象')
    topics=data['topics']
    if not isinstance(topics,list) or not topics:
        raise ValueError('话题划分为空，不能判定素材不合格')
    allowed_starts=set(sentence_starts(cues))
    starts=[]
    for topic in topics:
        if (not isinstance(topic,dict) or set(topic)!={'start','topic'}
                or type(topic['start']) is not int or not 0<=topic['start']<len(cues)
                or (not starts and topic['start']!=0)
                or (starts and topic['start']<=starts[-1])
                or topic['start'] not in allowed_starts
                or not isinstance(topic['topic'],str) or not topic['topic'].strip()):
            raise ValueError('话题起点不连续、重复或切断原句')
        starts.append(topic['start'])
    topics=[dict(t,end=starts[i+1]-1 if i+1<len(starts) else len(cues)-1)
            for i,t in enumerate(topics)]
    verdicts = data['verdicts']
    if not isinstance(verdicts, dict) or set(verdicts) != {str(c['candidate_id']) for c in choices}:
        raise ValueError('逐项审查未覆盖本批全部候选ID')
    if any(not isinstance(v, str) or v not in ACCEPT and v not in REJECT for v in verdicts.values()):
        raise ValueError('逐项审查包含未知或矛盾的判定')
    effective=dict(verdicts)
    for choice in choices:
        if not any(t['start']<=choice['start']<=choice['end']<=t['end'] for t in topics):
            effective[str(choice['candidate_id'])]='reject_mixed_topics'
    return effective, topics, verdicts


def reviewed_topics(cues):
    """Negative-only editorial evidence for the exact reviewed 715 transcript.

    Cue 27 introduces the separate arbitrage question. Neither topic lasts 120s.
    This cannot approve a range or apply to changed words/timestamps.
    Source: BV1ixfuBYEiX; evidence run 34559152818, original ASR from 34543016471.
    """
    digest=hashlib.sha256(json.dumps(cues,ensure_ascii=False,sort_keys=True,
                                    separators=(',',':')).encode()).hexdigest()
    if digest != '6c13f1a0fa429c549f6e470e3412e9ce13abec63581ce7f42060f1202e74e093':
        return None
    return [dict(start=0,end=26,topic='资本市场投资回报与发行估值'),
            dict(start=27,end=61,topic='无风险套利与恒大债券经历')]
