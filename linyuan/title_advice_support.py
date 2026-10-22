"""Require selected guest evidence for explicit negative trading advice.

This bounded check does not infer advice from opportunity, bubbles, or a
speaker's personal unwillingness. Role binding remains the source reader's job.
"""
import re


ADVICE = (r'(?:(?:不建议|不推荐|不主张|不要|不宜|不应该|不应当|不应)'
          r'[^。！？!?；;，,]{0,10}?|别(?:再|去|急着|盲目|轻易|随便|马上|现在){0,3})')
ACTIONS = (
    r'(?:进入市场|进市场|入市|进场|参与|买入|购买|投资|买|进)',
    r'(?:卖出|减仓|清仓|退出|离场|出场|卖)',
    r'(?:持有|持仓)',
)


def error(title, cover, evidence):
    for action in ACTIONS:
        pattern = ADVICE + action
        for copy in (title, cover):
            if not re.search(pattern, copy):
                continue
            supported = False
            for quote in evidence:
                for clause in re.findall(r'[^。！？!?；;，,]+[。！？!?；;，,]?', quote):
                    # Questions/denials mentioning advice are not endorsements.
                    if (re.search(r'不(?:是|代表|等于)|没有(?:说|建议)|并非|是否|是不是|您|你觉得', clause)
                            or re.search(r'[？?]', clause)):
                        continue
                    if re.search(pattern, clause):
                        supported = True
                        break
                if supported:
                    break
            if not supported:
                return '不建议等交易建议须有同方向的嘉宾原文证据；不能从主持人问题、泡沫或个人不想参与推导建议'
    return None
