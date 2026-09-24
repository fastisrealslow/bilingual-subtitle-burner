"""Conservative exclusions from explicit named interview hand-offs.

A visible target face can be listening while another guest speaks. These
source-text boundaries exclude that other guest's turn; they never certify
the remaining words as target speech or override audio/visual review.
"""
from bisect import bisect_right
from functools import lru_cache
import re

VERSION = 2026092301
# Require a recognizable surname so “就是林总” is not parsed as the name
# “就是林”. Unknown names stay unresolved; this is not a general NER claim.
SURNAMES = '林陈郭王李张刘赵黄周吴郑孙朱许何谢宋杨胡徐高罗马梁唐郑冯于董萧程曹袁邓方陆叶苏潘杜蒋沈韩曾彭吕蔡田丁任姜傅钟汪卢戴崔范石姚谭廖魏金邹熊秦邱侯江尹薛阎段雷龙黎史陶贺顾毛郝龚邵万钱严覃武孔向汤孟白常康赖施乔贾殷严鲁韦余夏戴牛洪龚段欧'
NAME = r'(?P<name>['+SURNAMES+r'][\u4e00-\u9fff]{0,2}?)(?:总|老师|先生|女士)'
PATTERNS = (
    re.compile(r'(?:请教|请问|问一下|问问|先问)(?:一下)?'+NAME+r'(?=[，,。！？!?您你]|$)'),
    re.compile(r'(?:^|[。！？!?])(?:嗯|好|好的|那|啊|呃|所以|但是|[，,\s]){0,12}'
               +NAME+r'(?:[，,]?(?:您|你)|[，,][^。！？!?]{0,45}?(?:请教|问一下|问问))'),
    re.compile(r'我(?:我|也|还|想)*(?:注意到|记得)[^。！？!?]{0,16}?'+NAME
               +r'[^。！？!?]{0,35}?(?:您|你)'),
    re.compile(r'让'+NAME+r'(?:回|谈|说|讲)'),
)


@lru_cache(maxsize=16)
def _handoffs(texts, speaker):
    source=''.join(texts)
    offsets=[];offset=0
    for text in texts:
        offsets.append(offset);offset+=len(text)
    if not offsets:return ()
    # Reported/quoted questions are not actual participant hand-offs.
    quotes=[m.span() for m in re.finditer(r'[“「『"].*?[”」』"]',source,re.S)]
    found={}
    for pattern in PATTERNS:
        for match in pattern.finditer(source):
            if any(a<=match.start()<b for a,b in quotes):continue
            start=bisect_right(offsets,match.start())-1
            name=match.group('name')
            # Honorific surname is accepted only as an exclusion reset. This
            # does not affirm voice identity or correct an ASR name hypothesis.
            target=name in {speaker,speaker[:1]}
            found[match.start()]=(start,name,target)
    # Several rules can recognize the same hand-off at nearby characters.
    result=[]
    for _,row in sorted(found.items()):
        if not result or row!=result[-1]:result.append(row)
    return tuple(result)


def named_handoffs(texts, speaker='林园'):
    return [dict(cue=i,addressee=name,target=target)
            for i,name,target in _handoffs(tuple(texts),speaker)]


def other_guest_indices(texts, speaker='林园'):
    turns=named_handoffs(texts,speaker);blocked=set()
    # A lone surname can be an ASR error (real #690 renders 林总 as 尹总).
    # Require an explicit target hand-off as well as another named addressee
    # before applying the multi-guest exclusion. No alias correction or voice
    # identity is inferred from an isolated honorific.
    if not any(t['target'] for t in turns) or not any(not t['target'] for t in turns):
        return blocked
    for i,turn in enumerate(turns):
        if turn['target']:continue
        end=turns[i+1]['cue'] if i+1<len(turns) else len(texts)
        blocked.update(range(turn['cue'],max(turn['cue']+1,end)))
    return blocked


def selection_error(cues, pick, speaker='林园'):
    blocked=other_guest_indices([c['text'] for c in cues],speaker)
    if any(i in blocked for i in range(pick['start'],pick['end']+1)):
        return '选段进入指名提问其他嘉宾的轮次；画面出现林园不代表这段原声属于林园，须另选明确转回本人的完整回答'
    return None
