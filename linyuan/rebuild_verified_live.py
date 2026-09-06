"""Rebuild a selected, already-identified live window with current captions/checks.

This recovery render retains its source identity and the duplicate-publication gate.
The original artifact and selected source range remain in the output metadata.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import subprocess

import produce_cn as P
import presentation as V


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--archive-dir', required=True)
    parser.add_argument('--out', required=True)
    parser.add_argument('--original-source', required=True)
    args = parser.parse_args()
    source, out = Path(args.archive_dir).resolve(), Path(args.out).resolve()
    original = Path(args.original_source).resolve()
    if (int(P.probe(original,'stream=width')),int(P.probe(original,'stream=height'))) != (1920,1080):
        raise ValueError('母片尺寸变更，已审核的人物取景不能直接复用')
    out.mkdir(parents=True, exist_ok=True)
    work = out / '_tmp'
    work.mkdir(exist_ok=True)
    old = next(m for m in json.loads((source/'meta.json').read_text()) if m['final']=='final_4.mp4')
    # The second original segment ends in an unfinished lead-in. Stop after the
    # completed preceding sentence, keeping its audio and all preceding words.
    base, end, offset = 1158.9, 1231.66, 63.9
    duration = end-base
    cues = json.loads((source/'_tmp/cues_raw.json').read_text())
    entries = [dict(start_sec=c['start']-base,end_sec=c['end']-base,zh=c['text'],en='')
               for c in cues if c['start']>=base-.01 and c['end']<=end+.01]
    layout = V.layout_for(720,1280,True)
    reviewed = json.loads((Path(__file__).parent/'rebuild-caption-groups.json').read_text())
    groups = P.apply_semantic_groups(entries,reviewed,layout['line_capacity'])
    (out/'subtitle-groups.json').write_text(json.dumps(reviewed,ensure_ascii=False,indent=2))
    ass = out/'subtitles.ass'
    P.make_ass(groups,ass,720,1280,card_style=True)
    transcript = ''.join(e['zh'] for e in entries)
    title = '林园：人的寿命会越来越长，你消费的就越多'
    error = P.title_quality_error(title,'林园',transcript)
    if error:
        raise ValueError(error)
    portrait = P.extract_audio_card_portrait(source/'_tmp/speaker_reference.jpg',work/'portrait.png')
    if portrait is None:
        raise P.VisualQualityError('原有权威人物参考图无法提取')
    background = work/'background.png'
    P.make_audio_card(background,'林园',title,width=720,height=1280,portrait_path=portrait,require_portrait=True)
    final = out/'linyuan-verified-live.mp4'
    subprocess.run(['ffmpeg','-y','-loglevel','error','-ss',str(base),'-i',str(original),
                    '-loop','1','-framerate','30','-i',str(background),
                    '-filter_complex',f'[0:v]crop=606:450:1120:330,scale=632:470,setsar=1,setpts=PTS-STARTPTS[live];[1:v][live]overlay=44:360,ass={ass}[outv]',
                    '-map','[outv]','-map','0:a:0','-c:v','libx264','-preset','veryfast','-crf','20',
                    '-r','30','-c:a','aac','-b:a','192k','-t',str(duration),'-movflags','+faststart',str(final)],
                   check=True,timeout=720)
    checks = V.verify_render(final,layout)
    checks.update(P.verify_live_region_after_render(final))
    subprocess.run(['ffmpeg','-v','error','-xerror','-i',str(final),'-f','null','-'],check=True,timeout=120)
    cover = out/'cover.jpg'
    P.make_audio_card(cover,'林园',title,width=1280,height=720,portrait_path=portrait,require_portrait=True,cover_style='light')
    preview, contact = P.make_review_assets(final,out,'',duration)
    meta = {**old,**checks,'slug':'ly-verified-rebuild-0906','final':final.name,'title':title,
            'duration_sec':round(duration,2),'segments':[{'start':base,'end':end,'reason':'医药行业前景完整观点'}],
            'desc':'林园访谈观点摘录，保留原讲话音频与真人动态画面。',
            'cover':cover.name,'cover_proof':json.loads(Path(str(cover)+'.proof.json').read_text()),
            'preview_30s':preview,'contact_sheet_6':contact,'layout_proof':layout,
            'subtitle_files':[ass.name],'subtitle_semantic_groups_verified':True,
            'fingerprints':P.build_content_fingerprints(final,transcript),
            'provenance':{'artifact_id':9986365708,'original_source':'https://www.bilibili.com/video/BV1kJuu6XEMS','crop':'606:450:1120:330','source_sha256':P._file_sha256(original),'rebuild':True},
            'generated_at':__import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat()}
    spec = importlib.util.spec_from_file_location('fc_quality',Path(__file__).parent/'fc/index.py')
    fc = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fc)
    error = fc.artifact_quality_error(meta)
    if error:
        raise ValueError(error)
    (out/'meta.json').write_text(json.dumps([meta],ensure_ascii=False,indent=2))
    proof={'passed':True,'full_decode':True,'live_video_ratio':1.0,'audio_card_ratio':0.0,
           'duration_sec':duration,'subtitle_groups':len(groups),'checks':checks,
           'sha256':meta['fingerprints']['sha256'],'artifact_quality_error':error,
           'source_artifact':9986365708,'caption_review':'reviewed complete intent groups; source characters validated','manual_visual_review':'pending'}
    (out/'verification.json').write_text(json.dumps(proof,ensure_ascii=False,indent=2))
    print(json.dumps(proof,ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
