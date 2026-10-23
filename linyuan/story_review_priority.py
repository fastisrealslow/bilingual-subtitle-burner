"""Scheduling hints only: no story verdict, source rewrite or extra budget."""
import re

POLICY='first_person_scene_review_order_v1'
SCENE=re.compile(r'当年|当时|那年|以前|曾经|有一次|那时候|年轻时|小时候|半夜|凌晨|晚上|早上|睡前|最近|去年|前年|(?:19|20)\d{2}年')
ACTION=re.compile(r'(?:我|我们)[^。！？!?]{0,60}(?:买|卖|持有|工作|上班|创业|看到|看过|听|去过|去了|参加|遇到|做过|做了)')
HYPOTHETICAL=re.compile(r'如果|假如|假设|将来|未来|以后|到时候|我(?:打算|准备|希望)')


def concrete_scene(cues,pick):
    text=''.join(c['text'] for c in cues[pick['start']:pick['end']+1])
    for sentence in re.findall(r'.+?(?:[。！？!?]+|$)',text,re.S):
        if re.search('[？?]',sentence) or HYPOTHETICAL.search(sentence):continue
        if SCENE.search(sentence) and ACTION.search(sentence):return True
    return False


def rank_candidates(cues,candidates):
    """Stable partition, not acceptance. Every candidate still needs the model.

    A concrete scene/first-person action can deserve earlier REVIEW when CPU
    time is bounded. It cannot prove speaker attribution, an actual story,
    accurate ASR, a good title, reusable rights, pixels or publishability.
    """
    return sorted(candidates,key=lambda p:not concrete_scene(cues,p))
