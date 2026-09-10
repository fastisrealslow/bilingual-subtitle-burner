"""Select complete, continuous source Q&A blocks without a text-model verdict.

This extracts actual questions and their following answers. It does not invent
semantic approval; all existing media, attribution and subtitle gates still run.
"""
import re
import editorial_policy as editorial
from headline_policy import quote_candidates, score

STOP = re.compile(r'[。！？!?][”’」』\"]?\s*$')
QUESTION = re.compile(r'请问|想问|请教|(?:您|你)[^。]*[？?]|(?:您|你).{0,100}(?:怎么|如何|能否|能不能|有没有|什么|哪些|怎样|多少|吗|呢|是否|建议|期待|分享|聊聊)|(?:怎么|如何|能不能).{0,40}(?:您|你)')
TOPIC_CHANGE = re.compile(r'(?:我们|咱们).{0,8}(?:下面|下一个|另外一个|换个).{0,5}话题|(?:我们|咱们).{0,5}(?:来聊聊|再来谈)')
FOLLOWUP = re.compile(r'(?:我|我们).{0,12}(?:有所担心|想追问|想进一步问|顺带.{0,3}问)|(?:这个|这一).{0,20}(?:我|我们).{0,5}(?:完全认同|完全同意)')
TRANSITION = re.compile(TOPIC_CHANGE.pattern+'|'+FOLLOWUP.pattern)
OUTRO = re.compile(r'今天的(?:对谈|访谈|对话)|这次的(?:对谈|访谈)|本期(?:节目|访谈).{0,8}(?:结束|到这里)|今天.{0,8}就到这里')


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
    units=units[:end]
    questions=[i for i,u in enumerate(units) if QUESTION.search(u['text'])]
    # Adjacent questions from the same interviewer turn belong together.
    starts=[i for k,i in enumerate(questions) if k==0 or i>questions[k-1]+1]
    transitions=[i for i,u in enumerate(units) if TRANSITION.search(u['text'])]
    options=[]
    for k,i in enumerate(starts):
        if k+1<len(starts):j=starts[k+1]-1
        elif whole_source:j=len(units)-1
        else:continue  # A mechanical chunk end is not a natural answer ending.
        j=min([j]+[t-1 for t in transitions if i<t<=j])
        # Keep the final answer, not the next unanswered question or farewell.
        while j>i and re.search(r'谢谢|感谢|祝愿|再见',units[j]['text']):j-=1
        if j<=i or QUESTION.search(units[j]['text']) or re.search(r'[？?]',units[j]['text']):continue
        a,b=units[i]['start'],units[j]['end']
        duration=cues[b]['end']-cues[a]['start']
        if not editorial.MIN_SECONDS<=duration<=330:continue
        text=''.join(c['text'] for c in cues[a:b+1])
        if editorial.transcript_integrity_error(text):continue
        quotes=quote_candidates(text)
        if not quotes:continue
        options.append((max(map(score,quotes)),dict(start=a,end=b,score=7,
            reason='保留原始提问和其后连续回答；未声称模型语义审核通过',
            selection_method='source_question_answer_v2')))
    return sorted([p for _,p in sorted(options,key=lambda x:x[0],reverse=True)[:limit]],key=lambda p:p['start'])
