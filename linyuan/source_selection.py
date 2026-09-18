"""Select complete, continuous source Q&A blocks without a text-model verdict.

This extracts actual questions and their following answers. It does not invent
semantic approval; all existing media, attribution and subtitle gates still run.
"""
import re
import editorial_policy as editorial
from headline_policy import quote_candidates, score, complete

VERSION = 27

STOP = re.compile(r'[。！？!?][”’」』\"]?\s*$')
QUESTION = re.compile(
    r'请问|想问|请教|您[^。]*[？?]|您.{0,100}(?:怎么|如何|能否|能不能|有没有|什么|哪些|怎样|多少|吗|呢|是否|建议|期待|分享|聊聊)'
    r'|你(?:们)?(?:是|当时|现在|未来|到底|最看好|对|认为|觉得|能|会|怎|如|在|的)[^。]{0,100}(?:怎么|如何|能不能|有没有|什么|哪些|怎样|多少|谁|吗|呢|是否|建议|期待|分享|聊聊)[^。！？?]*[？?]'
    r'|(?:怎么|如何|能不能).{0,40}您'
    # #942 starts with “免税行业的机会，你怎么看？”: do not consume 怎
    # and then require a second 怎么 as the longer 你... pattern does.
    r'|你(?:们)?(?:怎么看|怎么想|如何看待|有什么看法)[^。！？?]*[？?]'
    r'|(?:有|有的)投资者问|(?:主持人|网友|观众).{0,6}(?:提问|问到|想问)'
    r'|你还认为[^。]{0,100}[？?]'
    r'|(?:继续|再).{0,8}(?:给我们|给大家).{0,6}(?:讲讲|说说)'
    # Broadcast interviewers often omit 您/你. Require a host/topic prompt
    # as well as an interrogative; a guest's 是吧/为什么 stays in the answer.
    r'|(?:好[，,]|好的|另外|我们(?:最近讨论|就放眼|在操作中)|如果真的要投资)'
    r'.{0,220}(?:是不是|会不会|哪些|什么方法|怎么样|哪一条|哪一句|能否)[^。！？?]*[？?]'
    r'|(?:那么就目前来看|当前配置|如果用一句话来指导投资者|哪一句话来体现)'
    r'[^。！？?]{0,100}[？?]'
    # Interviewers sometimes omit 您/你 and ask directly which factors to
    # watch when selecting a stock/company in an industry. Require both the
    # investment object and the concrete “关注哪/什么” request so a guest's
    # rhetorical question is not promoted into a host turn.
    r'|(?:股票|公司|行业)[^。！？?]{0,120}关注(?:哪|什么)[^。！？?]{0,30}[？?]'
    # Source100 #6 uses short topic cards without 您/你. Missing these
    # questions falsely joined seven unrelated answers into a 195s clip.
    r'|^(?:对[^。！？?]{1,30}(?:有什么|有何)(?:投资)?(?:建议|看法|期待)'
    r'|有哪些[^。！？?]{1,30}值得推荐'
    r'|(?:年轻|普通|新入市的)(?:股民|投资者)[^。！？?]{0,12}该怎么[^。！？?]{1,25}'
    r'|想成为[^。！？?]{1,20}需要做到[^。！？?]{1,20}'
    r'|未来(?:中国|我国)?(?:股市|资本市场)[^。！？?]{0,30}吗'
    r'|(?:个股涨跌|股市大势)[^。！？?]{1,40}有没有[^。！？?]{1,20}'
    r'|最近[^。！？?]{0,12}有什么[^。！？?]{0,12}新的(?:认识|看法)吗)[？?]$')
TOPIC_CHANGE = re.compile(r'(?:我们|咱们).{0,8}(?:下面|下一个|另外一个|换个).{0,5}话题|(?:我们|咱们).{0,5}(?:来聊聊|再来谈)')
FOLLOWUP = re.compile(r'(?:我|我们).{0,12}(?:有所担心|想追问|想进一步问|顺带.{0,3}问)|(?:这个|这一).{0,20}(?:我|我们).{0,5}(?:完全认同|完全同意)')
TRANSITION = re.compile(TOPIC_CHANGE.pattern+'|'+FOLLOWUP.pattern)
# A final bare modal question has no object or predicate (e.g. “它有没有。”).
# It is an ASR/truncated-source tail, not a complete ending. Remove only that
# trailing sentence; never shorten an interior sentence or a complete phrase
# such as “有没有价值”。
INCOMPLETE_TAIL = re.compile(r'(?:有没有|能不能|会不会|是不是|要不要|可不可以)[。！!]*\s*$')
SPEECH_CHANGE = re.compile(
    r'(?:我|我们)(?:接下来|下面|现在).{0,12}(?:谈谈|讲讲|说说|介绍一下)'
    r'|(?:我|我们).{0,4}对(?:今天|当前|现在)的?[^。！？]{0,15}(?:市场|经济)'
    r'.{0,15}(?:判断|看法|分析)'
    # Reviewed source100 #79 is a continuous keynote, not an interview. These
    # are its literal speaker-announced boundaries: first from market timing to
    # what to invest in, then from the industry case to medical-domain support.
    # They prevent arbitrary fixed-length slicing while exposing two complete
    # 120-330s source spans to every unchanged media/editorial gate.
    r'|我再讲一下[^。！？]{0,24}接下来我们应该投什么'
    r'|我指的是药物[，,]一定是药物'
    # #79 changes both subject and camera after completing the domestic-crisis
    # examples. End that complete statement before the source switches to a
    # wide stage shot; do not weaken the continuous-face requirement.
    r'|这是境内的[，,]境外的'
    # Reviewed CPU transcripts #7/#68 explicitly announce these chapter
    # changes. Generic rhetorical questions are not chapter boundaries.
    r'|那么我就详细就讲一下'
    r'|那么我们又找到了一个今天投资'
    r'|我再讲一下为什么我说重要'
    r'|那我们怎么想呢[？?]所以未来的投资方向'
    r'|那剩下的就是你[，,]你就炒股')

# Long keynotes also announce numbered chapters without phrasing them as a
# question.  These are observable source boundaries, not semantic guesses or
# fixed-duration cuts.  Keep the expressions narrow: a casual “first/second”
# inside an example must not split a speech.
KEYNOTE_SECTION = re.compile(
    r'(?:那么)?投资啊[^。！？]{0,24}今天讲第一最重要的'
    r'|那么第二个[，,][^。！？]{0,24}投资要赚大钱'
    r'|(?:好了[，,]?)?接下来(?:就是)?我们要谈成长性'
    r'|(?:好了[，,]?)?接下来我就讲这个投资')
OUTRO = re.compile(
    r'(?:本期|今天的|这次的|本次)(?:节目|对谈|访谈|对话).{0,12}(?:结束|到这里|告一段落)'
    r'|今天.{0,8}就到这里|由于时间.{0,12}(?:不再|结束)'
    r'|今天交流了非常多.{0,12}收获'
    # Reviewed #686: the host's retrospective begins before the formal goodbye.
    r'|今天的对谈.{0,16}林总其实很克制'
    # #68 ends before the host resumes. Its final 114.72s chapter must not
    # borrow the outro/host narration to reach the unchanged 120s minimum.
    r'|我今天就讲这么多')
PROMOTIONAL_REINTRO = re.compile(r'^大家好[，,]我是.{1,8}[，,].{0,30}(?:股东大会|直播)')
HOST_BRIDGE = re.compile(
    r'^(?:啊[，,]?|嗯[，,]?|那|好的[，,]?)*'
    r'(?:感谢林总|谢谢林总|林总(?:也|是|阐述|提到)|小林总也是|您时刻提醒我们)'
    r'|^我们都知道林总|^(?:我看|看)(?:你|您)之前(?:也有|有|说)'
    r'|^(?:嗯[，,]?|好[，,]?|呃[，,]?|那么)*我们知道(?:现在|呢)'
    r'|(?:好的[，,]?好[，,]?|好[，,]那么)(?:那么)?我们(?:说现在|知道现在)')


# Follow-up turns may clarify the same subject; a new question alone is not
# evidence of a new topic. These anchors are deliberately concrete. Broad
# words such as 投资/市场/公司 must never join unrelated answers.
TOPIC_ANCHORS = (
    ('科技', '人工智能', '机器人', 'AI'),
    ('创新药',), ('医药', '药品'), ('中药', '中成药'),
    ('股息', '分红'), ('关税',), ('核心资产',),
    ('茅台',), ('五粮液',), ('片仔癀',), ('房地产',),
)
NEW_SUBJECT = re.compile(r'除了|另外|最后|再问一个|换.{0,4}话题|来谈谈|来聊聊|但我们今天采访')


def question_unit(text):
    # Quoted examples and a speaker's rhetorical self-questions are not a host
    # turn. Explicit 您/请问 remains a real question even in a long sentence.
    if not re.search(r'您|请问|请教', text) and re.search(
            r'比如|就像|我说[：:]|很多人问我|所以很多人问我|我的意思', text):
        return False
    return bool(QUESTION.search(text))


def speech_opening(text):
    # Headline fluency is stricter than spoken source fluency. Keep every byte
    # of a topical spoken opening, including hesitations and rhetorical 是吧.
    normalized=re.sub(r'(?:[，,]?(?:是吧|对吧)[？?])$', '。', text)
    normalized=re.sub(r'^(?:嗯|啊|呃)[，, ]*', '', normalized)
    if complete(normalized.strip('。！？!?')):
        return True
    if re.match(r'^(?:它|他|她|这|那|因为|所以|但是|并|虽然)',normalized):
        return False
    return bool(re.match(r'^(?:我(?:们)?(?:今年|认为|今天|特别|对)|三十.{0,8}以上的人)',normalized)
        and re.search(r'医药|中药|中医|投资|创业|消费|资产|股票|行业',normalized)
        and re.search(r'有效|重要|认为|不要|别|应该|方向|增长|有|是',normalized)
        and STOP.search(normalized))


def topic_anchors(text):
    text=re.sub(r'非科技(?:股)?','',text)
    return {i for i,words in enumerate(TOPIC_ANCHORS) if any(w in text for w in words)}


def same_topic_followup(first, following):
    if NEW_SUBJECT.search(following) or TOPIC_CHANGE.search(following):
        return False
    anchors=topic_anchors(first)
    return bool(anchors and anchors & topic_anchors(following))


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
            question=next((j for j in range(i,len(units)) if question_unit(units[j]['text'])),None)
            if question is None or question==len(units)-1:
                return '片尾带入下一问的铺垫却没有回答，不能借主持人问题凑时长'
    return None


def select(cues, limit=2, whole_source=False, diagnostics=None):
    units=sentence_units(cues)
    # A host's closing narration is not the guest's final answer. It must not
    # turn a short farewell into a 120-second "guest" clip.
    end=next((i for i,u in enumerate(units) if OUTRO.search(u['text'])
              or i>0 and PROMOTIONAL_REINTRO.search(u['text'])),len(units))
    natural_end=(end<len(units) or bool(whole_source and units and units[-1]['end']==len(cues)-1))
    units=units[:end]
    questions=[i for i,u in enumerate(units) if question_unit(u['text'])]
    # Adjacent questions from the same interviewer turn belong together.
    starts=[i for k,i in enumerate(questions) if k==0 or
            (i>questions[k-1]+1 and not all(re.search(r'[？?]$',units[t]['text'])
                for t in range(questions[k-1]+1,i)))]
    # Keep a host's lead-in with that question, not with the preceding answer.
    leadin=re.compile(r'采访您|^我们看其实|^那我们知道林|^那这个.{0,20}(?:问题|行业|个股)')
    question_starts=list(starts)
    for k,q in enumerate(question_starts):
        lower=(question_starts[k-1]+1 if k else 0)
        for t in range(max(lower,q-3),q):
            if leadin.search(units[t]['text']) and not question_unit(units[t]['text']):
                starts[k]=t;break
    transitions=[i for i,u in enumerate(units) if TRANSITION.search(u['text'])]
    options=[]
    if diagnostics is not None:
        diagnostics.update(selector_version=VERSION,cue_count=len(cues),sentence_count=len(units),
            question_count=len(questions),whole_source=whole_source,natural_end=natural_end,
            candidates=[])
    def reject(kind,a,b,reason):
        if diagnostics is not None:
            diagnostics['candidates'].append(dict(kind=kind,start=cues[a]['start'],
                end=cues[b]['end'],duration=round(cues[b]['end']-cues[a]['start'],3),reason=reason))
    turn_ends=[]
    for k,i in enumerate(starts):
        j=(starts[k+1]-1 if k+1<len(starts) else len(units)-1 if natural_end else None)
        if j is None:
            turn_ends.append(None);continue
        j=min([j]+[t-1 for t in transitions if i<t<=j])
        j=min([j]+[t-1 for t in range(i+1,j+1) if HOST_BRIDGE.search(units[t]['text'])])
        while j>i and (HOST_BRIDGE.search(units[j]['text'])
                or re.search(r'谢谢|感谢|祝愿|再见',units[j]['text'])
                or INCOMPLETE_TAIL.search(units[j]['text'])):j-=1
        turn_ends.append(j)
    spans=[(i,turn_ends[k]) for k,i in enumerate(starts)]
    for k,i in enumerate(starts):
        # A complete, independently long-enough answer is already a candidate.
        # Sharing an industry keyword with the next question is not a reason
        # to merge it again: overlapping copies crowd out later clean answers.
        # Keep the existing same-topic recovery only for short source turns.
        own_end=turn_ends[k]
        if own_end is None:continue
        if cues[units[own_end]['end']]['end']-cues[units[i]['start']]['start']>=editorial.MIN_SECONDS:
            continue
        anchor=''.join(u['text'] for u in units[i:question_starts[k]+1])
        for n in range(k+1,min(len(starts),k+5)):
            q=starts[n]
            # Include immediately preceding host context in the comparison,
            # but never include that preamble as the previous answer's ending.
            following=''.join(u['text'] for u in units[q:question_starts[n]+1])
            for t in range(max(starts[n-1]+1,q-2),q):
                if re.match(r'^(?:那这个|那我们|那所以|但是我们|我们看|那林总)',units[t]['text']):
                    following=units[t]['text']+following
            if not same_topic_followup(anchor,following):break
            j=turn_ends[n]
            if j is None or any(i<t<=q for t in transitions):break
            if cues[units[j]['end']]['end']-cues[units[i]['start']]['start']>330:break
            spans.append((i,j))
    for i,j in spans:
        if j is None:continue
        if j<=i or question_unit(units[j]['text']) or re.search(r'[？?]',units[j]['text']):continue
        a,b=units[i]['start'],units[j]['end']
        duration=cues[b]['end']-cues[a]['start']
        if not editorial.MIN_SECONDS<=duration<=330:
            reject('question_answer',a,b,'duration_outside_120_330');continue
        if boundary_error(cues,dict(start=a,end=b)):
            reject('question_answer',a,b,'incomplete_boundary');continue
        text=''.join(c['text'] for c in cues[a:b+1])
        if editorial.transcript_integrity_error(text):
            reject('question_answer',a,b,'transcript_integrity');continue
        quotes=quote_candidates(text)
        if not quotes:
            reject('question_answer',a,b,'no_supported_title_quote');continue
        options.append((max(map(score,quotes)),dict(start=a,end=b,score=7,
            reason='保留原始提问和其后连续回答；未声称模型语义审核通过',
            selection_method='source_question_answer_v2')))
    # Short, uninterrupted source speeches need no invented interviewer.
    # Only the actual source end or an explicit topic change closes a speech;
    # a 3-minute chunk edge or a row crossing 120s never does. Question-bearing
    # sections stay on the Q&A path above, so they cannot borrow another answer.
    cuts=sorted(set([0]+[i for i,u in enumerate(units) if
        TOPIC_CHANGE.search(u['text']) or SPEECH_CHANGE.search(u['text'])
        or KEYNOTE_SECTION.search(u['text'])]))
    for k,i in enumerate(cuts):
        if k+1<len(cuts):j=cuts[k+1]-1
        elif natural_end:j=len(units)-1
        else:continue
        while j>i and (re.fullmatch(r'(?:好吧[，,]?|好的[，,]?|啊[，,]?)*(?:谢谢|感谢)(?:林总|大家|您)?[。！!]*',units[j]['text'])
                or INCOMPLETE_TAIL.search(units[j]['text'])):j-=1
        if j<=i or any(i<=q<=j for q in questions):continue
        if not (whole_source or i>0):continue
        if not (SPEECH_CHANGE.search(units[i]['text']) or
                KEYNOTE_SECTION.search(units[i]['text']) or
                speech_opening(units[i]['text'])):continue
        if re.search(r'[？?]',units[j]['text']) or HOST_BRIDGE.search(units[j]['text']):continue
        a,b=units[i]['start'],units[j]['end']
        duration=cues[b]['end']-cues[a]['start']
        if not editorial.MIN_SECONDS<=duration<=330:
            reject('speech',a,b,'duration_outside_120_330');continue
        text=''.join(c['text'] for c in cues[a:b+1])
        if editorial.transcript_integrity_error(text) or boundary_error(cues,dict(start=a,end=b)):continue
        quotes=quote_candidates(text)
        if not quotes:continue
        options.append((max(map(score,quotes)),dict(start=a,end=b,score=7,
            reason='保留源片完整连续陈述及自然句界；未声称模型语义审核通过',
            selection_method='source_continuous_speech_v1')))
    # limit=None exposes every structurally valid interval to picture ranking.
    # Never change an answer boundary merely to fit a cleaner frame.
    ranked=[dict(p,editorial_rank=rank) for rank,(_,p) in
            enumerate(sorted(options,key=lambda x:x[0],reverse=True))]
    selected=sorted(ranked[:limit],key=lambda p:p['start'])
    if diagnostics is not None:
        diagnostics.update(accepted=len(selected),picks=selected,
            outcome='selected' if selected else 'no_structural_candidate')
    return selected
