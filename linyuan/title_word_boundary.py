"""Screen literal copy that ends inside a source noun; never repair the source."""
import re
from functools import lru_cache


@lru_cache(maxsize=32)
def _noun_interiors(source):
    import jieba.posseg
    interiors = {}
    offset = 0
    for token in jieba.posseg.cut(source):
        end = offset + len(token.word)
        if token.flag.startswith('n') and len(token.word) >= 2:
            for point in range(offset + 1, end):
                interiors[point] = token.word
        offset = end
    return interiors


def source_word_boundary_error(title, cover, transcript):
    """Require long literal context; paraphrases still need semantic review.

    A standalone occurrence of the same suffix is counterevidence, so a
    shorter expression genuinely present in the source is not prohibited.
    Segmentation is a rejection diagnostic, never publication approval.
    """
    source = re.sub(r'\s+', '', str(transcript or ''))
    interiors = _noun_interiors(source)
    for text in (title, cover):
        for clause in re.split(r'[，,。；;：:！？!?\n]', str(text or '')):
            clause = re.sub(r'\s+', '', clause).strip('“”「」\"')
            # Eight source-contiguous characters avoid matching isolated
            # characters, abbreviations, or an unrelated mention of a noun.
            for size in range(len(clause), 7, -1):
                suffix = clause[-size:]
                ends = [m.start() + size for m in re.finditer('(?=' + re.escape(suffix) + ')', source)]
                if not ends:
                    continue
                if all(end in interiors for end in ends):
                    word = interiors[ends[0]]
                    return f'标题或封面在原文词语「{word}」中间截断，须重新选择完整表达；不猜改原文'
                break
    return None
