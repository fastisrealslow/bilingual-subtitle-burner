"""Share existing publisher text duplicate rules with source preflight.

Only move rejection earlier. Missing fingerprints are not approval and every
final media/rights/content check remains required. No source or ASR aliases.
"""


def iter_published_parts(state):
    seen=set()
    for slug,info in (state.get('published') or {}).items():
        parts=info.get('parts') or []
        for part in parts if parts else [info]:
            if part.get('status')=='skipped':continue
            marker=(part.get('bvid') or info.get('bvid') or '',
                    part.get('title') or info.get('title') or '')
            if marker in seen:continue
            seen.add(marker)
            yield slug,dict(bvid=marker[0],title=marker[1],
                ts=part.get('ts') or info.get('ts') or 0,
                fingerprints=part.get('fingerprints') or info.get('fingerprints') or {})


def transcript_duplicate(current,previous):
    if not current or not previous:return None
    cur_ngrams=set(current.get('transcript_ngrams') or [])
    old_ngrams=set(previous.get('transcript_ngrams') or [])
    shared=len(cur_ngrams & old_ngrams)
    containment=shared/max(1,min(len(cur_ngrams),len(old_ngrams)))
    if shared>=8 and containment>=.45:
        return f'转写片段重合 {containment:.0%}'
    cur_text=current.get('transcript_simhash') or []
    old_text=previous.get('transcript_simhash') or []
    def distance(a,b):
        try:return (int(a,16)^int(b,16)).bit_count()
        except (TypeError,ValueError):return 10**9
    text_ratio=(sum(any(distance(a,b)<=10 for b in old_text) for a in cur_text)/len(cur_text)
                if cur_text and old_text else 0.)
    text_matches=round(text_ratio*len(cur_text))
    if text_ratio>=.60 and text_matches>=min(2,len(cur_text)):
        return f'转写内容重合 {text_ratio:.0%}'
    return None


def find_transcript_duplicate(current,state):
    for slug,old in iter_published_parts(state):
        reason=transcript_duplicate(current,old['fingerprints'])
        if reason:return dict(slug=slug,bvid=old['bvid'],reason=reason)
    return None
