"""Select complete, continuous source Q&A blocks without a text-model verdict.

This extracts actual questions and their following answers. It does not invent
semantic approval; all existing media, attribution and subtitle gates still run.
"""
import re
import editorial_policy as editorial
from headline_policy import quote_candidates, score, complete

VERSION = 16

STOP = re.compile(r'[。！？!?][”’」』\"]?\s*$')
QUESTION = re.compile(
    r'请问|想问|请教|您[^。]*[？?]|您.{0,100}(?:怎么|如何|能否|能不能|有没有|什么|哪些|怎样|多少|吗|呢|是否|建议|期待|分享|聊聊)'
    r'|你(?:们)?(?:是|当时|现在|未来|到底|最看好|对|认为|觉得|能|会|怎|如|在|的)[^。]{0,100}(?:怎么|如何|能不能|有没有|什么|哪些|怎样|多少|吗|呢|是否|建议|期待|分享|聊聊|[？?])'
    r'|(?:怎么|如何|能不能).{0,40}您'
    r'|(?:有|有的)投资者问|(?:主持人|网友|观众).{0,6}(?:提问|问到|想问)'
    r'|你还认为[^。]{0,100}[？?]'
    r'|(?:继续|再).{0,8}(?:给我们|给大家).{0,6}(?:讲讲|说说)')
TOPIC_CHANGE = re.compile(r'(?:我们|咱们).{0,8}(?:下面|下一个|另外一个|换个).{0,5}话题|(?:我们|咱们).{0,5}(?:来聊聊|再来谈)')
FOLLOWUP = re.compile(r'(?:我|我们).{0,12}(?:有所担心|想追问|想进一步问|顺带.{0,3}问)|(?:这个|这一).{0,20}(?:我|我们).{0,5}(?:完全认同|完全同意)')
TRANSITION = re.compile(TOPIC_CHANGE.pattern+'|'+FOLLOWUP.pattern)
OUTRO = re.compile(
    r'(?:本期|今天的|这次的|本次)(?:节目|对谈|访谈|对话).{0,12}(?:结束|到这里|告一段落)'
    r'|今天.{0,8}就到这里|由于时间.{0,12}(?:不再|结束)'
    r'|今天交流了非常多.{0,12}收获'
    # Reviewed #686: the host's retrospective begins before the formal goodbye.
    r'|今天的对谈.{0,16}林总其实很克制')
HOST_BRIDGE = re.compile(
    r'^(?:啊[，,]?|嗯[，,]?|那|好的[，,]?)*'
    r'(?:感谢林总|谢谢林总|林总(?:也|是|阐述|提到)|小林总也是|您时刻提醒我们)'
    r'|^我们都知道林总')


def sentence_units(cues):
    units=[]; start=0; text=''
    for i,c in enumerate(cues):
        text+=c['text']
        if STOP.search(text):
            units.append(dict(start=start,end=i,text=text))
            start=i+1; text=''
    return units


def boundary_error(cues,pick):
    """Apply observable boundaries to model/cache candidates too.

    Same-topic follow-up Q&A is allowed when its answer is retained. A host's
    preamble alone cannot supply the missing seconds of the preceding answer.
    This is a structural check, not a claimed semantic-model approval.
    """
    selected=cues[pick['start']:pick['end']+1]
    units=sentence_units(selected)
    for i,u in enumerate(units):
        if OUTRO.search(u['text']):return '选段包含主持人结束语，不能当作嘉宾回答凑时长'
        if i and TOPIC_CHANGE.search(u['text']):return '选段跨越明确的换题语，须按完整话题重新选择'
        if FOLLOWUP.search(u['text']):
            question=next((j for j in range(i,len(units)) if QUESTION.search(units[j]['text'])),None)
            if question is None or question==len(units)-1:
                return '片尾带入下一问的铺垫却没有回答，不能借主持人问题凑时长'
    return None


def select(cues, limit=2, whole_source=False):
    units=sentence_units(cues)
    # A host's closing narration is not the guest's final answer. It must not
    # turn a short farewell into a 120-second "guest" clip.
    end=next((i for i,u in enumerate(units) if OUTRO.search(u['text'])),len(units))
    natural_end=(end<len(units) or bool(whole_source and units and units[-1]['end']==len(cues)-1))
    units=units[:end]
    questions=[i for i,u in enumerate(units) if QUESTION.search(u['text'])]
    # Adjacent questions from the same interviewer turn belong together.
    starts=[i for k,i in enumerate(questions) if k==0 or i>questions[k-1]+1]
    transitions=[i for i,u in enumerate(units) if TRANSITION.search(u['text'])]
    options=[]
    for k,i in enumerate(starts):
        if k+1<len(starts):j=starts[k+1]-1
        elif natural_end:j=len(units)-1
        else:continue  # A mechanical chunk end is not a natural answer ending.
        j=min([j]+[t-1 for t in transitions if i<t<=j])
        # A multi-sentence host summary belongs to the next interviewer turn.
        # Looking only at the last sentence lets its unmarked continuation
        # inflate a short guest answer (#891 risk discussion).
        j=min([j]+[t-1 for t in range(i+1,j+1) if HOST_BRIDGE.search(units[t]['text'])])
        # Keep the final answer, not the next unanswered question or farewell.
        while j>i and (HOST_BRIDGE.search(units[j]['text'])
                      or re.search(r'谢谢|感谢|祝愿|再见',units[j]['text'])):j-=1
        if j<=i or QUESTION.search(units[j]['text']) or re.search(r'[？?]',units[j]['text']):continue
        a,b=units[i]['start'],units[j]['end']
        duration=cues[b]['end']-cues[a]['start']
        if not editorial.MIN_SECONDS<=duration<=330:continue
        if boundary_error(cues,dict(start=a,end=b)):continue
        text=''.join(c['text'] for c in cues[a:b+1])
        if editorial.transcript_integrity_error(text):continue
        quotes=quote_candidates(text)
        if not quotes:continue
        options.append((max(map(score,quotes)),dict(start=a,end=b,score=7,
            reason='保留原始提问和其后连续回答；未声称模型语义审核通过',
            selection_method='source_question_answer_v2')))
    # Short, uninterrupted source speeches need no invented interviewer.
    # Only the actual source end or an explicit topic change closes a speech;
    # a 3-minute chunk edge or a row crossing 120s never does. Question-bearing
    # sections stay on the Q&A path above, so they cannot borrow another answer.
    cuts=sorted(set([0]+[i for i,u in enumerate(units) if TOPIC_CHANGE.search(u['text'])]))
    for k,i in enumerate(cuts):
        if k+1<len(cuts):j=cuts[k+1]-1
        elif natural_end:j=len(units)-1
        else:continue
        if j<=i or any(i<=q<=j for q in questions):continue
        if not (whole_source or i>0):continue
        if not complete(units[i]['text'].strip('。！？!?')):continue
        if re.search(r'[？?]',units[j]['text']) or HOST_BRIDGE.search(units[j]['text']):continue
        a,b=units[i]['start'],units[j]['end']
        duration=cues[b]['end']-cues[a]['start']
        if not editorial.MIN_SECONDS<=duration<=330:continue
        text=''.join(c['text'] for c in cues[a:b+1])
        if editorial.transcript_integrity_error(text) or boundary_error(cues,dict(start=a,end=b)):continue
        quotes=quote_candidates(text)
        if not quotes:continue
        options.append((max(map(score,quotes)),dict(start=a,end=b,score=7,
            reason='保留源片完整连续陈述及自然句界；未声称模型语义审核通过',
            selection_method='source_continuous_speech_v1')))
    return sorted([p for _,p in sorted(options,key=lambda x:x[0],reverse=True)[:limit]],key=lambda p:p['start'])
