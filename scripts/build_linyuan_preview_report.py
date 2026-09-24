#!/usr/bin/env python3
"""Build a reproducible viewing report from actual files, never production inputs."""
import argparse
import hashlib
import html
import json
import os
from pathlib import Path
from urllib.parse import quote


def read(path,default=None):
    return json.loads(path.read_text()) if path.is_file() else default


def actual_rows(metadata):
    """Only expose local videos whose bytes match production metadata."""
    data=read(metadata,[])
    rows=data if isinstance(data,list) else [data]
    result=[]
    for row in rows:
        name=row.get('final','')
        if not name or Path(name).name!=name:continue
        video=metadata.parent/name
        digest=(row.get('fingerprints') or {}).get('sha256')
        if not video.is_file() or hashlib.sha256(video.read_bytes()).hexdigest()!=digest:continue
        result.append((row,video))
    return result


def build(preview_root,baseline_metadata,reference_root,reference_index,output):
    esc=lambda v:html.escape(str(v),quote=True)
    url=lambda p:quote(os.path.relpath(p,output.parent),safe='/')
    def player(video,cover=None):
        poster=' poster="'+esc(url(cover))+'"' if cover and cover.is_file() else ''
        return ('<video controls playsinline preload="none"'+poster+' src="'+esc(url(video))+'"></video>'
            '<p><a href="'+esc(url(video))+'" target="_blank" rel="noopener">单独播放 / 下载原文件</a></p>')
    def card(row,video,label):
        cover=video.parent/row.get('cover','')
        valid_cover=cover.is_file() and cover.parent==video.parent
        return ('<article><small>'+esc(label)+'</small><h2>'+esc(row.get('title',''))+'</h2>'
            +player(video,cover if valid_cover else None)+'<p>实际封面文案：'+esc(row.get('cover_title',''))+'</p>'
            +('<details><summary>实际封面图片</summary><img loading="lazy" src="'+esc(url(cover))+'"></details>' if valid_cover else '')
            +'<p>时长 '+esc(row.get('duration_sec','未知'))+' 秒</p><code>'+esc(row['fingerprints']['sha256'][:16])+'</code></article>')
    originals=actual_rows(baseline_metadata)
    references=[]
    for row in read(reference_index,{'rows':[]})['rows']:
        for proof_path in reference_root.glob('**/*'+row['bvid']+'/inspection.json'):
            proof=read(proof_path,{})
            video=proof_path.parent/'full-review.mp4'
            if proof.get('complete_timeline') and video.is_file():
                references.append((row,proof,video));break
    status=read(preview_root/'status.json',{})
    articles=[];seen=set()
    for metadata in sorted(preview_root.glob('**/preview-deliver-*/meta.json')):
        for row,video in actual_rows(metadata):
            digest=row['fingerprints']['sha256']
            if digest in seen:continue
            seen.add(digest)
            run=row.get('slug','').removeprefix('preview-')
            rejected=(str(status.get('producer_run'))==run and status.get('status')=='rejected_editorial_quality')
            verdict='编辑验收拒绝，禁止发布' if rejected else '自动产物：待核对实际内容，未据此认定可发布'
            note='<p class="notice">'+verdict+'</p>'
            if rejected:note+='<ul>'+''.join('<li>'+esc(x)+'</li>' for x in status.get('rejection_reasons',[]))+'</ul>'
            same=[(r,v) for r,v in originals if r.get('source_sha256')==row.get('source_sha256')]
            def overlap(item):
                return sum(max(0,min(a['end'],b['end'])-max(a['start'],b['start']))
                    for a in row.get('segments',[]) for b in item[0].get('segments',[]))
            before=max(same,key=overlap) if same else (originals[0] if originals else None)
            baseline=card(*before,'线上版本实际产物；按同源时间重叠匹配，不代表均已发布') if before else '<article>缺少可核对的线上原文件</article>'
            ref=min(references,key=lambda r:abs(float(r[1]['duration'])-float(row.get('duration_sec',0)))) if references else None
            reference=('<article><small>园园形式参考，按时长接近匹配；非同素材 A/B</small><h2>'+esc(ref[0]['title'])+'</h2>'
                +player(ref[2],ref[2].parent/'frame-00.jpg')+'<a href="'+esc(ref[0]['url'])+'">查看原投稿</a><p>'
                +esc(ref[0].get('observation',''))+'</p></article>') if ref else '<article>参考完整视频尚未取得</article>'
            articles.append('<section><h2>自动预览 '+esc(run)+'</h2>'+note+'<div class="grid">'+baseline+card(row,video,'全自动预览实际产物')+reference+'</div></section>')
    current=status.get('next_attempt',{})
    body='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>全自动出片实测</title><style>
body{margin:0;background:#f2f3ef;color:#182b32;font:16px/1.65 system-ui}main{max-width:1450px;margin:auto;padding:28px}.grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:18px}article{background:white;padding:18px;border-radius:10px;min-width:0}h1{font-size:32px}h2{font-size:20px}video,img{width:100%;max-height:350px;object-fit:contain;background:#111}a{color:#125c70}.notice{padding:16px;background:#fff1d4}small,code{color:#556970}td,th{padding:8px 14px;text-align:left;border-bottom:1px solid #ccd4d1}table{border-collapse:collapse}section{margin:32px 0}@media(max-width:850px){.grid{grid-template-columns:1fr}}</style><main><h1>线上 · 全自动预览 · 园园参考</h1>
<p>直接展示实际 MP4、实际封面和生成标题。缺文件或视频哈希不符时不生成播放器。此页不提供人工标题、字幕或选段给生产系统。</p>
<p class="notice">这里是单源预览，不能据此计算固定 100 条素材的出片率；历史 11% / 17% 也不代表本轮全自动流程的成绩。工作流通过、内容验收通过、发布成功分别核对。</p>'''
    if current.get('run'):
        body+='<p>后续任务 <a href="https://github.com/fastisrealslow/bilingual-subtitle-burner/actions/runs/'+esc(current['run'])+'">'+esc(current['run'])+'</a> · '+esc(current.get('status','未知'))+' · '+esc(current.get('model',''))+'</p>'
    body+=''.join(articles) or '<p>尚无文件哈希核对通过的自动产物。</p>'
    snapshot=read(preview_root/'source-snapshot/audit.json',{})
    if snapshot:
        from datetime import datetime
        from zoneinfo import ZoneInfo
        checked=datetime.fromtimestamp(snapshot['checked_at'],ZoneInfo('Asia/Shanghai')).isoformat(timespec='seconds')
        body+='<section><h2>现有素材库快照</h2><p>'+esc(checked)+' · 主线 '+esc(snapshot.get('snapshot_commit','')[:7])+'</p>'
        body+='<p>共 '+esc(snapshot.get('records'))+' 条记录。以下“时长符合”只是取源候选条件，不代表已下载、内容合格或已经出片；参考账号记录不进入生产素材。</p>'
        body+='<div style="overflow:auto"><table><tr><th>来源</th><th>记录数</th><th>视频候选</th><th>已知时长符合</th><th>时长未知</th><th>仅供参考</th></tr>'
        for name,counts in snapshot.get('materials',{}).items():
            body+='<tr><td>'+esc(name)+'</td>'+''.join('<td>'+esc(counts.get(k,0))+'</td>' for k in ('records','video_candidates','duration_eligible','duration_unknown','reference_only'))+'</tr>'
        body+='</table></div><p>这是已有线上流程的快照，不是本轮新代码的出片率。<a href="'+esc(url(preview_root/'source-snapshot/audit.json'))+'">完整统计及失败阶段记录</a></p></section>'
    body+='''</main><script>document.querySelectorAll('video').forEach(v=>{v.addEventListener('play',()=>document.querySelectorAll('video').forEach(o=>{if(o!==v)o.pause()}));v.addEventListener('error',()=>{const p=document.createElement('p');p.textContent='播放器未能加载，请通过同目录的本地 HTTP 服务打开，或使用上方原文件链接。';v.after(p)},{once:true})});</script></html>'''
    output.parent.mkdir(parents=True,exist_ok=True);output.write_text(body)
    return len(seen)


if __name__=='__main__':
    ap=argparse.ArgumentParser()
    for name in ('preview-root','baseline-metadata','reference-root','reference-index','output'):
        ap.add_argument('--'+name,type=Path,required=True)
    args=ap.parse_args();print('Verified video files:',build(**vars(args)))
