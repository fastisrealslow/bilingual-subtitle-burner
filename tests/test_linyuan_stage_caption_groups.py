"""Regress actual source68's split words without inventing ASR corrections."""
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan'))
import produce_cn as P
import stage_context as S
import presentation as V
from caption_readability import display_payload_text, replay_edit_proof


def test_real_stage_asr_is_grouped_before_claiming_semantic_validation(tmp_path):
    root=Path(__file__).resolve().parents[1]
    corpus=json.loads((root/'linyuan/simulations/benchmark-20260921/title-sep23-corpus.json').read_text())
    raw=next(r['cues'] for r in corpus if r['id']=='source100-68')
    origin=raw[0]['start']
    entries=[dict(start_sec=c['start']-origin,end_sec=c['end']-origin,zh=c['text']) for c in raw]
    spec=V.layout_for(1280,720)
    spec.update(subtitle_region=dict(x=64,y=590,width=1152,height=112),subtitle_font_px=44,line_capacity=24,subtitle_style='light-panel-dark-text')
    grouped,proof=S.caption_plan(entries,spec,P)
    assert len(grouped)<len(entries)
    assert not any(r['zh'] in {'的','定','人多得很'} for r in grouped)
    assert any('指数都是不会错的' in r['zh'] for r in grouped)
    assert any('肯定' in r['zh'] for r in grouped)
    assert all(.25<=r['end_sec']-r['start_sec']<=8 for r in grouped)
    assert min(r['start_sec'] for r in grouped)>=0
    assert max(r['end_sec'] for r in grouped)<=raw[-1]['end']-origin
    original,display=replay_edit_proof(proof)
    assert original==''.join(c['text'] for c in raw)
    assert display_payload_text(display)==display_payload_text(''.join(r['zh'] for r in grouped))
    ass=tmp_path/'stage.ass';V.write_ass(grouped,ass,spec,'Noto Sans CJK SC')
    import editorial_policy as E
    assert display_payload_text(E.subtitle_files_text(tmp_path,[ass.name]))==display_payload_text(display)
    # Existing doubtful recognition is retained; this is layout, not ASR repair.
    assert '产产生' in ''.join(r['zh'] for r in grouped)
