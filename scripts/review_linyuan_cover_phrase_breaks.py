"""Render isolated cover-layout previews; never change accepted video assets."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import types

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'linyuan'))
import presentation as current
from produce_cn import extract_audio_card_portrait


def main():
    previous=types.ModuleType('previous_cover_presentation')
    old_source=subprocess.check_output(['git','show','0fba8b2:linyuan/presentation.py'],cwd=ROOT,text=True)
    exec(compile(old_source,'previous_cover_presentation.py','exec'),previous.__dict__)
    out=ROOT/'output/benchmark-20260921/cover-phrase-break-check'
    out.mkdir(parents=True,exist_ok=True)
    media=json.loads((ROOT/'output/benchmark-20260921/all-finals-regression-35921175272/media-verification.json').read_text())
    cases=json.loads((ROOT/'linyuan/simulations/benchmark-20260921/all-current-title-corpus.json').read_text())
    rows=[dict(id=r['source_id'],text=r['old_cover']) for r in cases]
    rows.extend(dict(id=r['id'],text=r['cover_title']) for r in media)
    changed=[]
    for row in rows:
        before={str(n):previous.cover_headline(row['text'],max_lines=n) for n in (2,3)}
        after={str(n):current.cover_headline(row['text'],max_lines=n) for n in (2,3)}
        for n,lines in after.items():
            assert ''.join(lines)==''.join(before[n])
            assert len(lines)<=int(n) and max(map(len,lines))<=9
        if before!=after:changed.append(dict(row,before=before,after=after))
    portrait=extract_audio_card_portrait(
        ROOT/'output/benchmark-20260921/readable-layout-35909339863/readable-layout-8/work/speaker_reference.jpg',
        out/'portrait.jpg')
    font=ROOT/'output/local-tools/fonts/NotoSansCJKsc-Bold.otf'
    previews=[]
    for row in media:
        if row['id'] not in (66,95):continue
        item=dict(source_id=row['id'],source_sha256=row['source_sha256'],video_sha256=row['sha256'],title=row['cover_title'],images={})
        for label,renderer in [('before',previous),('after',current)]:
            image,lines,size,boxes=renderer.dark_cover(portrait,row['cover_title'],'林园',str(font))
            path=out/(str(row['id'])+'-'+label+'.jpg')
            image.save(path,quality=95)
            proof=current.cover_proof(image,path,lines,size,boxes,style='dark')
            item['images'][label]=dict(file=str(path.relative_to(ROOT)),sha256=hashlib.sha256(path.read_bytes()).hexdigest(),lines=lines,proof=proof)
        previews.append(item)
    report=dict(cases=len(rows),changed=changed,previews=previews,source_yield_credit=False,
        accepted_assets_modified=False,rendered_video=False,
        note='同一资料照、文案、字体的排版预览；不是线上封面替换，也不是新成片。')
    (out/'review.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(dict(cases=len(rows),changed=len(changed),previews=len(previews))))


if __name__=='__main__':main()
