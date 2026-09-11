"""Exhaustive, non-contradictory judgments for real source ranges."""
import json

VERSION = 1
BATCH_SIZE = 48
ACCEPT = {f'accept_{score}': score for score in range(7, 11)}
REJECT = {
    'reject_opening': '开头缺必要上下文',
    'reject_ending': '结尾未自然结束',
    'reject_mixed_topics': '拼接了不同话题',
    'reject_explanation': '缺少观点的必要解释',
    'reject_low_value': '不足7分，不适合独立成片',
}


def schema(choices):
    fields = {str(c['candidate_id']): {'type': 'string', 'enum': list(ACCEPT)+list(REJECT)}
              for c in choices}
    return {'type': 'object', 'properties': {'verdicts': {
        'type': 'object', 'properties': fields, 'required': list(fields),
        'additionalProperties': False}}, 'required': ['verdicts'], 'additionalProperties': False}


def prompt(transcript, choices, speaker):
    return (f'你是{speaker}访谈编辑。逐项审核下面每个候选，完整原文只提供一次。'
        '每个候选的真实时长已由程序确认至少120秒；时长足够不代表内容合格。'
        '必须根据候选起止字幕之间的全部原话判断：开头独立可懂，一个完整主题，'
        '必要解释充分，结尾自然收束，并有至少7分的独立观看价值。'
        '同主题追问可以保留；不同问题拼接、寒暄、下一话题未说完都不能用来凑时长。'
        '不得猜测原片之外的解释，不得改写、补字或修改候选边界。'
        '每个候选只填一个判定：合格为accept_7、accept_8、accept_9或accept_10；'
        '不合格必须选reject_opening、reject_ending、reject_mixed_topics、'
        'reject_explanation或reject_low_value。不能只评价头两个候选。'
        '输出JSON对象verdicts，键是本批每个候选ID，值是上述判定；所有ID必须恰好出现一次。'
        '\n完整原文：\n'+transcript+'\n本批候选：\n'+json.dumps(choices, ensure_ascii=False))


def parse(answer, choices):
    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('逐项审查包含重复候选ID或字段')
            result[key] = value
        return result
    data = json.loads(answer, object_pairs_hook=unique_object)
    if not isinstance(data, dict) or set(data) != {'verdicts'}:
        raise ValueError('逐项审查缺少唯一的verdicts对象')
    verdicts = data['verdicts']
    if not isinstance(verdicts, dict) or set(verdicts) != {str(c['candidate_id']) for c in choices}:
        raise ValueError('逐项审查未覆盖本批全部候选ID')
    if any(not isinstance(v, str) or v not in ACCEPT and v not in REJECT for v in verdicts.values()):
        raise ValueError('逐项审查包含未知或矛盾的判定')
    return verdicts
