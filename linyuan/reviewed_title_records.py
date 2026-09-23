"""Retain inspected title choices for exact passages, with original provenance."""
import copy
import hashlib
import json
from pathlib import Path
import editorial_policy
import title_rewrite


def lookup(source_sha256, transcript, speaker, path=None):
    path=Path(path or Path(__file__).with_suffix('.json'))
    records=json.loads(path.read_text())
    digest=editorial_policy.text_digest(transcript)
    matches=[r for r in records if r['source_sha256']==source_sha256
             and r['transcript_sha256']==digest and r['speaker']==speaker]
    if not matches:
        return None
    if len(matches)!=1:
        raise ValueError('同母片同选段存在多份冲突的编辑标题记录')
    record=matches[0];result=copy.deepcopy(record['result'])
    if result['cover_title']!=result['title_rewrite'].get('cover'):
        raise ValueError('编辑封面文案与原始标题证据不一致')
    issue=title_rewrite.error(result['title'],result['title_rewrite'],transcript=transcript,speaker=speaker)
    if issue:
        raise ValueError('已核对标题不再满足当前证据检查：'+issue)
    result['title_quality_verified']=True
    result['packaging_method']='reviewed_title_reuse'
    proof=dict(record_sha256=hashlib.sha256(json.dumps(record,ensure_ascii=False,sort_keys=True).encode()).hexdigest(),
        source_sha256=source_sha256,transcript_sha256=digest,provenance=copy.deepcopy(record['provenance']),
        scope=record['scope'],editorial_approved=False)
    return result,proof
