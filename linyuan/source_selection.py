"""Select complete, continuous source Q&A blocks without a text-model verdict.

This extracts actual questions and their following answers. It does not invent
semantic approval; all existing media, attribution and subtitle gates still run.
"""
import re
import editorial_policy as editorial
from headline_policy import quote_candidates, score

STOP = re.compile(r'[。！？!?][”’」』\"]?\s*$')
QUESTION = re.compile(r'请问|想问|请教|您[^。]*[？?]|您.{0,100}(?:怎么|如何|能否|能不能|有没有|什么|哪些|怎样|多少|吗|呢|是否|建议|期待|分享|聊聊)|(?:怎么|如何|能不能).{0,40}您')


def sentence_units(cues):
    units=[]; start=0; text=''
    for i,c in enumerate(cues):
        text+=c['text']
        if STOP.search(text):
            units.append(dict(start=start,end=i,text=text))
            start=i+1; text=''
    return units


def select(cues, limit=2, whole_source=False):
    units=sentence_units(cues)
    questions=[i for i,u in enumerate(units) if QUESTION.search(u['text'])]
    # Adjacent questions from the same interviewer turn belong together.
    starts=[i for k,i in enumerate(questions) if k==0 or i>questions[k-1]+1]
    options=[]
    for k,i in enumerate(starts):
        if k+1<len(starts):j=starts[k+1]-1
        elif whole_source:j=len(units)-1
        else:continue  # A mechanical chunk end is not a natural answer ending.
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
            selection_method='source_question_answer_v1')))
    return sorted([p for _,p in sorted(options,key=lambda x:x[0],reverse=True)[:limit]],key=lambda p:p['start'])
