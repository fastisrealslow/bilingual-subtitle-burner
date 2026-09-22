"""Optional source question-card boundaries; never semantic/media approval.

Look only inside long gaps between complete ASR sentences. A question needs
matching central OCR on two separate frames. Card words remain visual context,
never guest quotations, ASR corrections or new spoken subtitles.
"""
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import time

VERSION=1
QUESTION=re.compile(r'什么|哪些|怎么|如何|怎样|是否|能否|有没有|[？?]')


def normalized(text):
    return re.sub(r'[^0-9A-Za-z\u4e00-\u9fff]','',str(text))


def agreed_question(samples):
    eligible=[s for s in samples if QUESTION.search(s.get('text',''))
              and 10<=len(normalized(s['text']))<=100]
    counts=Counter(normalized(s['text']) for s in eligible)
    for key,count in counts.most_common():
        hits=[s for s in eligible if normalized(s['text'])==key]
        if count>=2 and max(s['time'] for s in hits)-min(s['time'] for s in hits)>=.5:
            return dict(text=hits[0]['text'],samples=hits)
    return None


def chapter_ranges(cues,cards,min_seconds=20):
    import source_selection as ss
    import editorial_policy as ep
    units=ss.sentence_units(cues)
    starts={u['start']:u for u in units};ends={u['end'] for u in units}
    accepted=sorted([c for c in cards if c.get('question') and c['next_cue'] in starts],
                    key=lambda c:c['next_cue'])
    result=[]
    # Every observed long gap is a stop, even if its OCR failed. Never join
    # across an unread card to recover duration or pretend a topic was read.
    boundaries=sorted({c['next_cue'] for c in cards})
    for card in accepted:
        a=card['next_cue'];b=next((x-1 for x in boundaries if x>a),len(cues)-1)
        if b not in ends or b<a:continue
        seconds=cues[b]['end']-cues[a]['start']
        if not min_seconds<=seconds<=330:continue
        first=starts[a]['text']
        # The visual question supplies an observed chapter boundary; headline
        # fluency is not a spoken-answer boundary test. Keep spoken fillers,
        # but require an explicit subject and exclude dependent continuations.
        if (not ss.SPOKEN_SUBJECT.search(first) or ss.question_unit(first)
                or re.match(r'^(?:因为|所以|但是|它|他|她)',first)):
            continue
        last=next(u['text'] for u in units if u['end']==b)
        if ss.unresolved_question_tail(last):continue
        pick=dict(start=a,end=b,score=7,selection_method='source_visual_question_card_v1',
            visual_question=card['question']['text'],editorial_approved=False,
            reason='源画面提问卡两帧文字一致；保留其后原声至下一长停顿，仍需画面、归属与标题检查')
        if ss.boundary_error(cues,pick) or ep.transcript_integrity_error(''.join(c['text'] for c in cues[a:b+1])):continue
        result.append(pick)
    return result


def propose(src,cues,work,ocr,source_sha256,min_seconds=20,budget_seconds=120):
    import cv2
    import source_selection as ss
    work=Path(work);work.mkdir(parents=True,exist_ok=True)
    proof=dict(version=VERSION,source_sha256=source_sha256,
        cues_sha256=hashlib.sha256(json.dumps(cues,ensure_ascii=False,sort_keys=True).encode()).hexdigest(),
        cards=[],picks=[],editorial_approved=False,scope='Visual source boundaries only; OCR is not spoken text')
    target=work/'question-cards.json';start=time.monotonic()
    cap=cv2.VideoCapture(str(src))
    try:
        if not cap.isOpened():raise ValueError('source video could not be opened')
        units=ss.sentence_units(cues);ends={u['end'] for u in units};starts={u['start'] for u in units}
        gaps=[]
        for i,c in enumerate(cues):
            before=cues[i-1]['end'] if i else 0
            if c['start']-before>=5:
                gaps.append((i,before,c['start']))
        # Record ALL gaps before bounded sampling, so unexamined cards cannot
        # be crossed by the final sampled chapter when time/count limits apply.
        proof['cards']=[dict(next_cue=i,gap=[a,b],samples=[],question=None) for i,a,b in gaps]
        for card in proof['cards'][:16]:
            a,b=card['gap']
            i=card['next_cue']
            if b-a>30 or i not in starts or (i and i-1 not in ends):continue
            for frac in (.2,.35,.5,.65,.8):
                if time.monotonic()-start>budget_seconds:raise TimeoutError('question-card scan budget reached')
                t=a+(b-a)*frac;cap.set(cv2.CAP_PROP_POS_MSEC,t*1000);ok,frame=cap.read()
                if not ok:continue
                h,w=frame.shape[:2];recognized,_=ocr(frame);lines=[]
                for box,text,confidence in recognized or []:
                    xs=[v[0] for v in box];ys=[v[1] for v in box]
                    if confidence>=.85 and min(ys)>.22*h and max(ys)<.78*h and min(xs)>.12*w and max(xs)<.92*w:
                        lines.append((min(ys),min(xs),text))
                text=''.join(r[2] for r in sorted(lines))
                sample=dict(time=round(t,4),text=text)
                if QUESTION.search(text):
                    image=work/f'card-{card["next_cue"]}-{frac}.jpg';cv2.imwrite(str(image),frame)
                    sample.update(frame=image.name,frame_sha256=hashlib.sha256(image.read_bytes()).hexdigest())
                card['samples'].append(sample)
            card['question']=agreed_question(card['samples'])
        proof['complete_scan']=len(gaps)<=16
    except (ValueError,RuntimeError,TimeoutError,OSError,cv2.error) as exc:
        proof.update(complete_scan=False,error=f'{type(exc).__name__}: {exc}')
    finally:
        cap.release()
        proof['picks']=chapter_ranges(cues,proof['cards'],min_seconds)
        proof['seconds']=round(time.monotonic()-start,3)
        target.write_text(json.dumps(proof,ensure_ascii=False,indent=2)+'\n')
    return proof['picks']
