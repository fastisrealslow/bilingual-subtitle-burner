"""Exhaustive, non-contradictory judgments for real source ranges."""
import json

VERSION = 2
BATCH_SIZE = 48
ACCEPT = {f'accept_{score}': score for score in range(7, 11)}
REJECT = {
    'reject_opening': '开头缺必要上下文',
    'reject_ending': '结尾未自然结束',
    'reject_mixed_topics': '拼接了不同话题',
    'reject_explanation': '缺少观点的必要解释',
    'reject_low_value': '不足7分，不适合独立成片',
}


def schema(choices, cue_count):
    fields = {str(c['candidate_id']): {'type': 'string', 'enum': list(ACCEPT)+list(REJECT)}
              for c in choices}
    topic_fields={'start':{'type':'integer','minimum':0,'maximum':cue_count-1},
                  'end':{'type':'integer','minimum':0,'maximum':cue_count-1},
                  'topic':{'type':'string','minLength':1,'maxLength':50}}
    return {'type': 'object', 'properties': {
        'topics':{'type':'array','minItems':1,'maxItems':20,'items':{
            'type':'object','properties':topic_fields,'required':list(topic_fields),
            'additionalProperties':False}}, 'verdicts': {
        'type': 'object', 'properties': fields, 'required': list(fields),
        'additionalProperties': False}}, 'required': ['topics','verdicts'], 'additionalProperties': False}


def prompt(transcript, choices, speaker):
    return (f'你是{speaker}访谈编辑。先划分完整原文的话题，再逐项审核候选。'
        'topics必须覆盖全部原始字幕，按顺序连续、无重叠、无遗漏；start/end是原始字幕编号。'
        '每一项是一个可独立说明的主题，不得把所有财经讨论都笼统称为投资。'
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
        '输出JSON对象含topics和verdicts。topics每项start,end,topic；verdicts的键是本批每个候选ID，'
        '值是上述判定；所有ID必须恰好出现一次。'
        '\n完整原文：\n'+transcript+'\n本批候选：\n'+json.dumps(choices, ensure_ascii=False))


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
    import re
    # Use complete source sentences, including an unfinished final source row;
    # coverage alone never declares such a row a valid clip ending.
    ends={i for i,c in enumerate(cues) if re.search(r'[。！？!?][”’」』\"]?\s*$',c['text'])}
    ends.add(len(cues)-1)
    next_start=0
    for topic in topics:
        if (not isinstance(topic,dict) or set(topic)!={'start','end','topic'}
                or type(topic['start']) is not int or type(topic['end']) is not int
                or topic['start']!=next_start or not topic['start']<=topic['end']<len(cues)
                or topic['end'] not in ends or not isinstance(topic['topic'],str) or not topic['topic'].strip()):
            raise ValueError('话题范围不连续、重叠或切断原句')
        next_start=topic['end']+1
    if next_start!=len(cues):
        raise ValueError('话题划分没有覆盖完整原文')
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
