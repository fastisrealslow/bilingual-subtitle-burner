"""Source-bound replay of the actual failed caption seam; no model/media approval."""
import hashlib
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def replay(path):
    import qwen_asr_evidence as Q
    from produce_cn import _merge_cues,_funasr_tokens_to_cues,cue_caption_entry
    from caption_readability import clean_entries,replay_edit_proof
    fixture=json.loads((ROOT/'tests/fixtures/linyuan_asr_seam_20261030.json').read_text())
    raw=Path(path).read_bytes()
    if hashlib.sha256(raw).hexdigest()!=fixture['raw_report_sha256']:
        raise ValueError('Actual failed alignment report differs')
    report=json.loads(raw)
    if report['source_video_sha256']!=fixture['source_video_sha256']:
        raise ValueError('Actual failed source differs')
    before=json.dumps(report,sort_keys=True)
    words=Q.validated_words([report],report['source_pcm_sha256'],report['source_video_sha256'],
                            report['duration'],model_id=report['model_id'])
    def entries(selected):
        timed=Q.punctuated_words([report],selected)
        cues=_merge_cues(_funasr_tokens_to_cues([w['text'] for w in timed],
            [w['start'] for w in timed],0,report['duration'],end_timestamps=[w['end'] for w in timed]))
        return [cue_caption_entry(c) for c in cues
                if c['start']>=1009.48-.001 and c['end']<=1144.68+.001]
    original=[w for c in report['chunks'] for w in c['words']
              if c['core_start']<=w['start']<c['core_end']]
    try:clean_entries(entries(original))
    except ValueError as exc:
        if str(exc)!='字幕逐字时间倒退或重叠':raise
    else:raise ValueError('The actual failed seam was not reproduced')
    selected=entries(words)
    if len(selected)!=43:raise ValueError('Actual source selection changed unexpectedly')
    _,proof=clean_entries(selected)
    replay_edit_proof(proof)
    _,changes=Q.ownership_ranges(report['chunks'])
    expected=[dict(core_boundary=1080,selected_boundary=1081.56,shared_context=['瞎','说','我','说'])]
    if changes!=expected or before!=json.dumps(report,sort_keys=True):
        raise ValueError('Unproven source ownership change')
    return dict(passed=True,source_run_id=fixture['source_run_id'],
        source_sha256=report['source_video_sha256'],raw_report_sha256=fixture['raw_report_sha256'],
        original_report_unchanged=True,caption_edit_proof_replayed=True,selected_cues=len(selected),
        ownership_changes=changes,media_approved=False,asr_inference_rerun=False,
        repair_sha256=hashlib.sha256((ROOT/'linyuan/qwen_asr_evidence.py').read_bytes()).hexdigest(),
        producer_sha256=hashlib.sha256((ROOT/'linyuan/produce_cn.py').read_bytes()).hexdigest())


def proof_error(proof):
    fixture=json.loads((ROOT/'tests/fixtures/linyuan_asr_seam_20261030.json').read_text())
    if (proof.get('passed') is not True or proof.get('source_run_id')!=fixture['source_run_id']
            or proof.get('source_sha256')!=fixture['source_video_sha256']
            or proof.get('raw_report_sha256')!=fixture['raw_report_sha256']
            or proof.get('original_report_unchanged') is not True
            or proof.get('caption_edit_proof_replayed') is not True
            or proof.get('media_approved') is not False or proof.get('asr_inference_rerun') is not False
            or proof.get('selected_cues')!=43
            or proof.get('ownership_changes')!=[dict(core_boundary=1080,selected_boundary=1081.56,
                                                    shared_context=['瞎','说','我','说'])]):
        return 'actual_asr_seam_not_verified'
    for filename,key in [('qwen_asr_evidence.py','repair_sha256'),('produce_cn.py','producer_sha256')]:
        if proof.get(key)!=hashlib.sha256((ROOT/'linyuan'/filename).read_bytes()).hexdigest():
            return 'asr_repair_code_differs'
    return None
