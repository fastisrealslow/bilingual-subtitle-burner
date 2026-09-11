"""Keep an audited unpublished stock interval fixed while rebuilding its media."""
import json
from pathlib import Path
import editorial_policy as editorial
import headline_policy

PLAN=Path(__file__).parent/'stock-upgrade-0911.json'


def plan_for(slug):
    if not PLAN.exists():return None
    return next((p for p in json.loads(PLAN.read_text())['batches'] if p['new_slug']==slug),None)


def source_ranges(cues,source_sha,slug,plan=None):
    plan=plan if plan is not None else plan_for(slug)
    if plan is None:return None
    if plan['new_slug']!=slug or source_sha!=plan['source_sha256']:
        raise ValueError('库存升级母片指纹或目标批次不一致')
    ranges=[]
    for part in plan['parts']:
        segments=part['segments']
        if len(segments)!=1:raise ValueError('库存升级只能保留原有连续选段')
        segment=segments[0]
        starts=[i for i,c in enumerate(cues) if abs(c['start']-segment['start'])<.011]
        ends=[i for i,c in enumerate(cues) if abs(c['end']-segment['end'])<.011]
        if len(starts)!=1 or len(ends)!=1 or ends[0]<starts[0]:
            raise ValueError('库存升级原始字幕边界已变化，不能猜测或重新选片')
        a,b=starts[0],ends[0]
        pick=dict(start=0,end=b-a,score=7,reason=segment.get('reason') or '保留已验收库存的连续源区间，重新执行成片质检')
        if part.get('render_mode')=='audio_card':pick['stock_original_mode']='audio_card'
        if part.get('title') and headline_policy.complete(headline_policy.body(part['title'])):
            from produce_cn import title_quality_error
            transcript=''.join(c['text'] for c in cues[a:b+1])
            if not title_quality_error(part['title'],'林园',transcript):
                pick['editorial_title']=part['title']
        editorial.range_seconds(cues[a:b+1],pick)
        ranges.append((a,b,[pick]))
    return ranges
