"""Select complete, continuous source Q&A blocks without a text-model verdict.

This extracts actual questions and their following answers. It does not invent
semantic approval; all existing media, attribution and subtitle gates still run.
"""
import re
import editorial_policy as editorial
from headline_policy import quote_candidates, score, complete

VERSION = 39

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
TOPIC_CHANGE = re.compile(
    r'(?:我们|咱们).{0,8}(?:下面|下一个|另外一个|换个).{0,5}话题|(?:我们|咱们).{0,5}(?:来聊聊|再来谈)'
    # An interviewer can announce the next topic in a statement before the
    # next question. Keep its lead-in out of the previous answer, regardless
    # of the subject matter or source. Explicit same-topic continuation stays.
    r'|^(?:好(?:的)?|那(?:么)?|其实|[啊呃嗯，,\s])*(?:我们|咱们)'
    r'(?:还|是|也|想|要|先|再|[啊呃嗯，,\s]){0,16}'
    r'(?:接下来|下面|接着)(?:来|[啊呃嗯，,\s]){0,4}(?:聊|谈|讨论)'
    r'(?!(?:一?下)?[，,\s]*(?:同一|这个|这一|刚才的))(?:一?下)?')
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
# Source46 ends its answer, then starts a separate recap at an exact cue.
# Treat the explicit new section as a boundary, without guessing its speaker.
# A long, complete recap may still be selected independently below.
SUMMARY_SECTION = re.compile(r'^(?:简单|我(?:们)?(?:再|来)?|那我(?:们)?(?:再|来)?)?'
    r'总结(?:一)?下(?:这)?几个关键词[：:]')
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
HOST_RECAP = r'(?:^|[。！？!?])(?:[啊嗯呃][。！？!?，,\s]*)?好的[，,\s]*(?:刚才|刚刚|前面)(?:您)?(?:说到|提到|谈到)'
HOST_BRIDGE = re.compile(
    r'总结来看[^。！？!?]{0,24}(?:两位|几位|各位)(?:两位|几位|的|这个|[，,])*嘉宾'
    r'|'
    r'^(?:啊[，,]?|嗯[，,]?|那|好(?:的|了)?[，,]?)*'
    r'(?:感谢林总|谢谢林总|林总(?:也|是|阐述|提到)|小林总也是|您时刻提醒我们)'
    r'|^我们都知道林总|^(?:我看|看)(?:你|您)之前(?:也有|有|说)'
    r'|^(?:嗯[，,]?|好[，,]?|呃[，,]?|那么)*我们知道(?:现在|呢)'
    r'|(?:好的[，,]?好[，,]?|好[，,]那么)(?:那么)?我们(?:说现在|知道现在)'
    r'|^(?:嗯[，,]?|啊[，,]?)*刚才我们在说'
    r'|'+HOST_RECAP)


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
SPOKEN_SUBJECT = re.compile(r'医药|中药|消费|股票|股市|港股|A股|企业|公司|银行|科技|人工智能|投资|股息|分红|股价|估值|波段|短线')
HOST_PREMISE = re.compile(
    r'^(?:(?:但是|但|那么|那|嗯|呃)[，,、 ]?)*'
    r'(?:我记得(?:你|您)说过|我们知道(?:你|您)之前|'
    r'(?:在)?我们看到(?:你|您)(?:整个的|的)(?:投资|经历|选择))')
BACKREF_QUESTION = re.compile(r'^(?:这{1,2}|那{1,2})(?:一)?点(?:是)?(?:怎么|如何)[^。！？?]{0,40}[？?]')


def host_premise_start(units, cues, question, lower=0):
    """Recover an explicit addressed premise, never invent an antecedent.

    Source311 starts a selected question with “但在你那里…是吗” after
    dropping the host's 牛熊 premise. Its next “这一点是怎么做到的” is
    another question only because a direct addressed host premise precedes it.
    Bound the lookup by real sentence ends, time, and intervening questions.
    """
    for i in range(question - 1, max(lower, question - 3) - 1, -1):
        if (cues[units[question]['start']]['start']-cues[units[i]['start']]['start']>30
                or question_unit(units[i]['text'])):
            break
        if HOST_PREMISE.search(units[i]['text']):
            return i
    return None


def promotional_cta(text):
    # Actual library308 ends with a seller's 小黄车 pitch. Preserve the raw
    # source, but do not make that extra commercial sentence our own outro.
    if re.search(r'比如|举例|有人说|他说|不要|不能|不是',text):
        return False
    return bool(re.fullmatch(
        r'(?:[^。！？!?]{1,18}[，,])?(?:下[面方](?:的)?(?:小黄车|购物车)|(?:小黄车|购物车|商品橱窗))'
        r'.{0,6}(?:有售|购买|下单|拍下)[。！!]*',text))


def contextual_self_answer(units,cues,index):
    """Keep a source's opening question AND answer when its topic follows."""
    text=units[index]['text']
    if not re.fullmatch(r'(?:这个|该)行业[^。！？!?]{0,12}是不是[^。！？!?]{1,12}[？?]是[^。！？!?]{1,12}[。！!]',text):
        return False
    start=cues[units[index]['start']]['start']
    # A sentence beginning at 12s can finish after 20s. Judge topic presence
    # from its original timed cue, not the end of the whole joined sentence.
    opening=''.join(c['text'] for c in cues[units[index]['start']:units[-1]['end']+1]
                    if c['start']-start<=20)
    return bool(re.search(r'AI|人工智能|医药|白酒|半导体|新能源|食品饮料',opening))


def question_unit(text):
    # Quoted examples and a speaker's rhetorical self-questions are not a host
    # turn. Explicit 您/请问 remains a real question even in a long sentence.
    if not re.search(r'您|请问|请教', text) and re.search(
            r'比如|就像|我说[：:]|很多人问我|所以很多人问我|我的意思', text):
        return False
    return bool(QUESTION.search(text))


def unresolved_question_tail(text):
    if not re.search(r'[？?]',text):return False
    if editorial.CONTENT_POLICY!='reference_v1':return True
    # Raw ASR can put a rhetorical “对吧？” and the next full statement in
    # one timestamped cue. Keep that cue; do not mistake an interior question
    # mark for an unanswered final question. A short “嗯/好的” is not an answer.
    after=re.split(r'[？?]',text)[-1]
    return len(re.sub(r'[\W_]','',after))<8 or not STOP.search(after)


def speech_opening(text):
    # Headline fluency is stricter than spoken source fluency. Keep every byte
    # of a topical spoken opening, including hesitations and rhetorical 是吧.
    normalized=re.sub(r'(?:[，,]?(?:是吧|对吧)[？?])$', '。', text)
    normalized=re.sub(r'^(?:嗯|啊|呃)[，, ]*', '', normalized)
    if complete(normalized.strip('。！？!?')):
        return True
    if editorial.CONTENT_POLICY == 'reference_v1':
        if re.match(r'^(?:作为我本人|对我来说|就我自己而言)[，,](?:我)?(?:没有|不会|不想|不|是|要)',normalized) and STOP.search(normalized):
            return True
        # Spoken openings are not permanent headlines. Fillers and a natural
        # “是…的” ending must not reject an otherwise explicit subject/claim.
        # This string is classification-only: original cues remain untouched.
        spoken=re.sub(r'[啊嗯呃呀](?=[，,。！？!?])','',normalized)
        spoken=re.sub(r'(是[^，,。！？!?]{1,14})的([。！？!?]?)$',r'\1\2',spoken)
        if SPOKEN_SUBJECT.search(spoken) and complete(spoken.strip('。！？!?')):
            return True
        if SPOKEN_SUBJECT.search(spoken) and (
                re.fullmatch(r'我(?:们)?(?:为什么|为何|怎么|如何)[^。！？!?]{2,40}[？?]',spoken)
                or re.fullmatch(r'我(?:们)?(?:从来|一直|现在|就|也|都)?(?:不|不会|不想)(?:做|买|卖|投|追|碰)[^，,。！？!?]{2,12}(?:[，,](?:不做|不买|不卖|不投))?[。！!]',spoken)):
            return True
        if re.match(r'^我(?:先|再)?(?:给(?:你|大家|你们))?(?:讲|说|举).{0,4}(?:故事|例子|经历)[。！!]$',spoken):
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


def declared_investment_sections(units,cues):
    """A named new sector plus an explicit personal investment stance.

    Library314 moves from tariffs to AI without announcing 换个话题. Only
    propose that original sentence boundary when a different topic precedes
    it and the speaker states a choice immediately afterwards. Mentioning a
    sector inside an example or adding a title keyword is not a boundary.
    """
    cuts=[]
    for i,u in enumerate(units):
        if not i or not re.match(r'^(?:人工智能|医药|白酒|科技|创新药|中药|房地产)(?:啊[，,]?|[，,])',u['text']):
            continue
        start=cues[u['start']]['start'];current=topic_anchors(u['text'])
        prior=''.join(v['text'] for v in units[:i] if start-cues[v['end']]['end']<=180)
        previous=topic_anchors(prior)
        if not current or not previous or current & previous:continue
        following=''.join(c['text'] for c in cues[u['start']:units[-1]['end']+1]
                          if c['start']-start<=30)
        if re.search(r'我(?:们)?(?:看好[^。！？!?]{0,20}[，,]但是我)?(?:不敢投|不投|只投|不会买|不买|不参与)',following):
            cuts.append(i)
    return cuts


def sentence_units(cues):
    units=[]; start=0; text=''
    for i,c in enumerate(cues):
        text+=c['text']
        if STOP.search(text):
            units.append(dict(start=start,end=i,text=text))
            start=i+1; text=''
    return units


def floor_handover_ranges(cues):
    """Observable microphone handovers, without assigning semantic approval.

    A sentence shared with the next speaker is excluded in full. Never invent
    an intra-cue timestamp to keep a few more seconds of the guest's ending.
    """
    units=sentence_units(cues)
    invitation=re.compile(r'把话筒交给|话筒交给|请.{1,12}(?:发言|谈谈|讲几句)')
    thanks=re.compile(r'(?:谢谢|感谢)(?:谢谢|感谢)?(?:我们)?[^，。！？!?]{1,10}(?:总|先生|老师|董事长)[啊，。！!,]')
    spans=[]
    for i,u in enumerate(units[:-1]):
        # A bare 有请 inside a quoted anecdote is insufficient. Require the
        # actual invitation immediately before it and an exact short cue.
        context=''.join(x['text'] for x in units[max(0,i-1):i+1])
        if not re.fullmatch(r'(?:嗯[，,。]?|好[，,。]?)*有请[。！!]',u['text'].strip()):continue
        if not invitation.search(context):continue
        for j in range(i+2,len(units)):
            if not thanks.search(units[j]['text']):continue
            # The host acknowledgement may be in the same ASR sentence as
            # the guest's final words. Retain only prior complete sentences.
            a,b=units[i+1]['start'],units[j-1]['end']
            spans.append((a,b))
            break
    return spans


def boundary_error(cues,pick):
    """Apply observable boundaries to model/cache candidates too.

    Same-topic follow-up Q&A is allowed when its answer is retained. A host's
    preamble alone cannot supply the missing seconds of the preceding answer.
    This is a structural check, not a claimed semantic-model approval.
    """
    from speaker_attribution import selection_error
    attribution_error=selection_error(cues,pick)
    if attribution_error:return attribution_error
    full_units=sentence_units(cues)
    for q,u in enumerate(full_units):
        if u['start']!=pick['start']:continue
        if (question_unit(u['text']) or BACKREF_QUESTION.search(u['text'])) and host_premise_start(full_units,cues,q) is not None:
            return '选段省略了主持人明确的提问前提；须从原始背景句开始，不以指代问题开场'
        break
    selected=cues[pick['start']:pick['end']+1]
    units=sentence_units(selected)
    if declared_investment_sections(units,selected):
        return '选段跨入明确新行业及个人投资表态；在原始话题句界分开，避免标题后半段才出现'
    for i,u in enumerate(units):
        if i and SUMMARY_SECTION.search(u['text']):
            return '选段跨入明确的总结章节；在原声边界分开，不能把总结开头接在上一段结论后'
        if promotional_cta(u['text']):return '选段包含原片带货收尾，须在原始句界结束，不能保留小黄车广告'
        if OUTRO.search(u['text']):return '选段包含主持人结束语，不能当作嘉宾回答凑时长'
        if i and TOPIC_CHANGE.search(u['text']):return '选段跨越明确的换题语，须按完整话题重新选择'
        if FOLLOWUP.search(u['text']) or (i and HOST_BRIDGE.search(u['text'])):
            question=next((j for j in range(i,len(units)) if question_unit(units[j]['text'])),None)
            if question is None or question==len(units)-1:
                return '片尾带入下一问的铺垫却没有回答，不能借主持人问题凑时长'
    return None


def select(cues, limit=2, whole_source=False, diagnostics=None):
    units=sentence_units(cues)
    # A host's closing narration is not the guest's final answer. It must not
    # turn a short farewell into a 120-second "guest" clip.
    end=next((i for i,u in enumerate(units) if OUTRO.search(u['text']) or promotional_cta(u['text'])
              or i>0 and PROMOTIONAL_REINTRO.search(u['text'])),len(units))
    natural_end=(end<len(units) or bool(whole_source and units and units[-1]['end']==len(cues)-1))
    units=units[:end]
    questions=[i for i,u in enumerate(units) if question_unit(u['text'])
               or BACKREF_QUESTION.search(u['text']) and host_premise_start(units,cues,i) is not None]
    # Adjacent questions from the same interviewer turn belong together.
    starts=[i for k,i in enumerate(questions) if k==0 or
            (i>questions[k-1]+1 and not all(re.search(r'[？?]$',units[t]['text'])
                for t in range(questions[k-1]+1,i)))]
    # Keep a host's lead-in with that question, not with the preceding answer.
    # A recap inside a sentence unit may share a raw ASR cue with the
    # preceding answer. Do not invent an intra-cue time or move the guest's
    # conclusion into the next question. That mixed unit cannot end a clip.
    leadin=re.compile(r'采访您|^我们看其实|^那我们知道林|^那这个.{0,20}(?:问题|行业|个股)|^'+HOST_RECAP)
    question_starts=list(starts)
    from speaker_attribution import named_handoffs
    handoff_cues={row['cue'] for row in named_handoffs([c['text'] for c in cues])}
    for k,q in enumerate(question_starts):
        lower=(question_starts[k-1]+1 if k else 0)
        premise=host_premise_start(units,cues,q,lower)
        if premise is not None:starts[k]=premise
        for t in range(max(lower,q-3),q):
            if leadin.search(units[t]['text']) and not question_unit(units[t]['text']):
                starts[k]=t;break
        # Keep the named hand-off's premise with its actual question. Real
        # source8 says 林总之前...四千五百点 before asking 您的这个观点...;
        # starting at that second sentence loses the numerical premise.
        named=[t for t in range(max(lower,q-3),q+1)
               if any(units[t]['start']<=c<=units[t]['end'] for c in handoff_cues)
               and cues[units[q]['start']]['start']-cues[units[t]['start']]['start']<=30
               and not any(question_unit(units[j]['text']) for j in range(t,q))]
        if named:starts[k]=min(starts[k],max(named))
    investment_sections=declared_investment_sections(units,cues) if editorial.CONTENT_POLICY=='reference_v1' else []
    transitions=sorted(set(investment_sections+[i for i,u in enumerate(units) if TRANSITION.search(u['text']) or SUMMARY_SECTION.search(u['text'])]))
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
        if j<=i or question_unit(units[j]['text']) or unresolved_question_tail(units[j]['text']):continue
        a,b=units[i]['start'],units[j]['end']
        duration=cues[b]['end']-cues[a]['start']
        if not editorial.MIN_SECONDS<=duration<=330:
            reject('question_answer',a,b,f'duration_outside_{editorial.MIN_SECONDS:g}_330');continue
        boundary_issue=boundary_error(cues,dict(start=a,end=b))
        if boundary_issue:
            reject('question_answer',a,b,boundary_issue);continue
        text=''.join(c['text'] for c in cues[a:b+1])
        if editorial.transcript_integrity_error(text):
            reject('question_answer',a,b,'transcript_integrity');continue
        quotes=quote_candidates(text)
        if not quotes and editorial.CONTENT_POLICY!='reference_v1':
            reject('question_answer',a,b,'no_supported_title_quote');continue
        # A complete Q&A need not contain a ready-made short headline. It will
        # still pass the independent title reader/writer and all media gates.
        # Prefer literal quote opportunities, but do not confuse their absence
        # with missing source context.
        options.append((max(map(score,quotes),default=0),dict(start=a,end=b,score=7,
            reason='保留原始提问和其后连续回答；未声称模型语义审核通过',
            selection_method='source_question_answer_v2')))
    # Short, uninterrupted source speeches need no invented interviewer.
    # Only the actual source end or an explicit topic change closes a speech;
    # a 3-minute chunk edge or a row crossing 120s never does. Question-bearing
    # sections stay on the Q&A path above, so they cannot borrow another answer.
    cuts=sorted(set([0]+investment_sections+[i for i,u in enumerate(units) if
        TOPIC_CHANGE.search(u['text']) or SPEECH_CHANGE.search(u['text'])
        or KEYNOTE_SECTION.search(u['text']) or SUMMARY_SECTION.search(u['text'])]))
    # An already short source may start with an answer dependent on a missing
    # question. Propose the first self-contained sentence in its opening,
    # retaining everything afterwards up to the original natural end. Never
    # pick a late punchline or join separate topics to reach a duration floor.
    if (editorial.CONTENT_POLICY=='reference_v1' and whole_source and natural_end
            and units and not questions and len(cuts)==1
            and cues[units[-1]['end']]['end']-cues[units[0]['start']]['start']<120
            and not speech_opening(units[0]['text'])
            and not contextual_self_answer(units,cues,0)):
        # Several short ASR sentences/fillers can precede the first complete
        # opening. Keep the existing 20-second budget, not an unrelated
        # two-sentence cap (real sources63/82 had a valid fourth sentence).
        for i in range(1,len(units)):
            if cues[units[i]['start']]['start']-cues[units[0]['start']]['start']>20:
                break
            opening=units[i]['text']
            if (cues[units[i]['start']]['start']-cues[units[0]['start']]['start']<=20
                    and SPOKEN_SUBJECT.search(opening)
                    and not re.match(r'^(?:人家|他们|这些|那些|这个|那个)',opening)
                    and speech_opening(opening)):
                cuts=[i];break
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
                speech_opening(units[i]['text']) or i in investment_sections or
                (whole_source and editorial.CONTENT_POLICY=='reference_v1' and contextual_self_answer(units,cues,i))):continue
        if unresolved_question_tail(units[j]['text']) or HOST_BRIDGE.search(units[j]['text']):continue
        a,b=units[i]['start'],units[j]['end']
        duration=cues[b]['end']-cues[a]['start']
        maximum=720 if whole_source and editorial.CONTENT_POLICY=='reference_v1' else 330
        if not editorial.MIN_SECONDS<=duration<=maximum:
            reject('speech',a,b,f'duration_outside_{editorial.MIN_SECONDS:g}_{maximum}');continue
        text=''.join(c['text'] for c in cues[a:b+1])
        if editorial.transcript_integrity_error(text) or boundary_error(cues,dict(start=a,end=b)):continue
        quotes=quote_candidates(text)
        if not quotes and editorial.CONTENT_POLICY!='reference_v1':continue
        options.append((max(map(score,quotes),default=0),dict(start=a,end=b,score=7,
            reason='保留源片完整连续陈述及自然句界；未声称模型语义审核通过',
            selection_method='source_continuous_speech_v1')))
    # Shareholder meetings use microphone handovers, not interview questions.
    # Source67 previously lost the whole 228s statement because neither the
    # opening host invitation nor the closing acknowledgement was recognized.
    # Offer this exact continuous range to the same argument/face/title gates.
    if whole_source and editorial.CONTENT_POLICY=='reference_v1':
        for a,b in floor_handover_ranges(cues):
            duration=cues[b]['end']-cues[a]['start']
            if not editorial.MIN_SECONDS<=duration<=330:continue
            text=''.join(c['text'] for c in cues[a:b+1])
            if editorial.transcript_integrity_error(text) or boundary_error(cues,dict(start=a,end=b)):continue
            quotes=quote_candidates(text)
            options.append((max(map(score,quotes),default=0),dict(start=a,end=b,score=7,
                reason='明确交接话筒后至主持人致谢前的完整句；仍需观点与身份审核',
                selection_method='source_floor_handover_v1')))
    # limit=None exposes every structurally valid interval to picture ranking.
    # Never change an answer boundary merely to fit a cleaner frame.
    ranked=[dict(p,editorial_rank=rank) for rank,(_,p) in
            enumerate(sorted(options,key=lambda x:x[0],reverse=True))]
    selected=sorted(ranked[:limit],key=lambda p:p['start'])
    if diagnostics is not None:
        diagnostics.update(accepted=len(selected),picks=selected,
            outcome='selected' if selected else 'no_structural_candidate')
    return selected
