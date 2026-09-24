"""Three playable columns with explicit version and missing-result evidence."""
from pathlib import Path
import html
import json
import os
import re

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'output/benchmark-20260921'
RECORDS=ROOT/'linyuan/simulations/benchmark-20260921'
esc=lambda value:html.escape(str(value))


def read(path, default=None):
    return json.loads(path.read_text()) if path.exists() else default


def subtitle_details(folder):
    # Reframed finals retain their own rendered ASS, not a portrait ASS alias.
    paths=sorted(folder.glob('landscape-subtitles*.ass')) or sorted(folder.glob('subtitles*.ass'))
    if not paths:return '<p class="small">本条未取得可展开的ASS字幕。</p>'
    lines=[]
    for path in paths:
        for line in path.read_text().splitlines():
            if not line.startswith('Dialogue:'):continue
            fields=line.split(',',9)
            if len(fields)==10:
                text=re.sub(r'\{[^}]*\}','',fields[9]).replace(r'\N',' / ')
                lines.append(fields[1]+' · '+text)
    return '<details><summary>展开实际字幕（'+str(len(lines))+'条）</summary><pre class="transcript">'+esc('\n'.join(lines))+'</pre></details>'


def build_threeway(reference_media, player):
    baseline_folder=ROOT/'output/baseline-comparison-20260921/results'
    current_folder=ROOT/'output/candidate100-35682602969/results'
    old={r['id']:r for r in read(baseline_folder/'media-verification.json',[]) if r['variant']=='baseline'}
    new={r['id']:r for r in read(current_folder/'media-verification.json',[])}
    judgments={r['id']:r for r in read(RECORDS/'production-pair-review.json',[])}
    for row in sorted(read(RECORDS/'targeted-production-review.json',[]),key=lambda x:int(x['run_id'])):
        if row['id'] in new and str(row['run_id'])==str(new[row['id']]['run_id']):judgments[row['id']]=row
    review=read(RECORDS/'sep23-review.json',{})
    for row in review.get('rows',[]):
        judgments[row['id']]=row
    for row in review.get('reference_overrides',[]):
        judgments[row['id']]={**judgments.get(row['id'],{}),
                              'reference_bvid':row['bvid']}
    # Selected, reviewed artifacts can replace the middle player. Keep cohort
    # statistics frozen; showing a later recovery does not rewrite its result.
    originals={}
    for override in review.get('threeway_overrides',[]):
        matches=[r for r in read(OUT/override['verification_file'],[])
                 if r['id']==override['id'] and r.get('sha256')==override['sha256']
                 and r.get('video_audio_full_decode')]
        if len(matches)!=1:continue
        ident=override['id']
        originals[ident]=new.get(ident)
        new[ident]=matches[0]
        judgments[ident]={**judgments.get(ident,{}),'known_quality_issue':True,
                          'comparison':override['comparison']}
    references={r['bvid']:r for r in read(RECORDS/'references-20.json',{'rows':[]})['rows']}
    references.update({r['bvid']:r for r in read(RECORDS/'references-latest-sep23.json',{'rows':[]})['rows']})

    def column(row, label, ident, folder, prefix):
        content='<div class="comparison-column"><h4>'+label+'</h4>'
        if row and row.get('video_audio_full_decode'):
            local=lambda key:os.path.relpath(ROOT/row[key],OUT)
            content+=player(local('file'),local('cover'),
                f"{row['duration']:.2f}秒 · {row['dimensions'][0]}×{row['dimensions'][1]} · 代码 {row['tested_sha'][:7]}")
            content+='<p class="copy-title">'+esc(row['title'])+'</p>'
            content+='<p class="small">封面字：'+esc(row.get('cover_title',''))+'</p>'
            content+=subtitle_details((ROOT/row['file']).parent)
            content+='<small>实际成片哈希已核对；完整音视频解码通过。内容质量见本行问题记录。</small>'
        else:
            paths=list(folder.glob(prefix+f'{ident:03d}-*/report.json'))
            report=read(paths[0],{}) if paths else {}
            content+='<div class="missing-video"><b>本批没有已核验成片</b><p>'+esc(
                ('报告：'+str(report.get('status'))+' / '+str(report.get('stage'))) if report else '本地尚未取得报告')+'</p>'
            if report.get('validation_error'):content+='<p>'+esc(report['validation_error'])+'</p>'
            content+='</div>'
        return content+'</div>'

    cards=[]
    for ident in sorted(set(old)|set(new)):
        a,b=old.get(ident),new.get(ident);note=judgments.get(ident,{})
        pair='both' if a and b else 'new' if b else 'missing'
        if a and b:
            source_note=('两边母片SHA相同；片段可能不同，请比较实际内容。' if a['source_sha256']==b['source_sha256']
                         else '两边母片字节不同，不计严格同母片胜负。')
        else:source_note='保留一侧无成片的情况，不用其他样片补空位。'
        content=column(a,'线上主线 · 固定版本复跑',ident,baseline_folder,'ab-report-baseline-sim-0916-')
        content+=column(b,'优化版 · 当前已取得的实片',ident,current_folder,'simulation-report-sim-0916-')
        if originals.get(ident):
            prior=originals[ident]
            content=content.removesuffix('</div>')+'<details><summary>保留固定100条批次的原始实片</summary>'+player(
                os.path.relpath(ROOT/prior['file'],OUT),os.path.relpath(ROOT/prior['cover'],OUT),
                '原批次 '+prior['tested_sha'][:7]+'；不以专项恢复回填成绩')+'</details></div>'
        bvid=note.get('reference_bvid')
        if bvid:
            title=references.get(bvid,{}).get('title')
            if not title:title=read(OUT/'reference'/bvid/'view.json',{}).get('title','园园参考')
            content+='<div class="comparison-column"><h4>园园 · 对应内容形式参考</h4>'+player(*reference_media(bvid))
            content+='<p><a href="https://www.bilibili.com/video/'+esc(bvid)+'">'+esc(title)+'</a></p><small>独立作品，不是同母片A/B；实际画面与音轨状态已标明，不冒充最高分辨率。</small></div>'
        else:content+='<div class="comparison-column"><h4>园园参考</h4><p>尚未建立可核对的对应项。</p></div>'
        warning=('<p class="warning"><strong>已确认存在质量问题：本条保留作失败对照，不能计编辑合格。</strong></p>'
                 if note.get('known_quality_issue') else '')
        cards.append('<article id="source-'+str(ident)+'" class="threeway-card" data-kind="'+pair+'"><h3>素材 '+str(ident)+'</h3><p class="small">'+source_note+'</p>'+warning+'<div class="threeway-grid">'+content+'</div><p class="review-note">'+esc(note.get('comparison','尚待逐条内容核对，不按自动出片判优。'))+'</p></article>')
    return '<section id="threeway"><h2>历史固定批次：主线、优化版、园园</h2><p>左列是主线固定版本复跑，中列是已核验的优化实片，右列是园园的主题或形式参考。每条保留实际版本和已发现的问题。</p><details><summary>查看版本、验收范围与统计口径</summary><p>中列以06815b4固定批次为基础，8、17、49、79补入后续实片；已有旧片仍可展开播放。所有成片已核对哈希、完整音视频解码、六帧及字幕，尚未逐秒听音验收。园园作品不是同素材A/B。播放器更新不回填固定批次成绩，技术出片也不等于编辑质量合格。</p></details><p><button onclick="threewayFilter(\'all\')">全部</button> <button onclick="threewayFilter(\'both\')">两版都有成片</button> <button onclick="threewayFilter(\'new\')">自动出片新增（含质量问题）</button> <button onclick="threewayFilter(\'missing\')">优化未出片</button></p>'+''.join(cards)+'</section>'


CSS='''.threeway-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:22px}.comparison-column{min-width:0}.comparison-column h4{padding:10px;background:#e8eee9;border-radius:5px}.threeway-card{margin:24px 0}.threeway-grid video{height:310px}.copy-title{font-weight:650}.review-note{border-left:4px solid #b28248;padding:12px 16px;background:#fff6e8}.missing-video{height:280px;padding:16px;box-sizing:border-box;background:#ebedeb;color:#52606a}.threeway-grid .transcript{font-size:13px}@media(max-width:700px){.threeway-grid{grid-template-columns:1fr}.threeway-grid video{height:330px}.missing-video{height:auto;min-height:120px}}'''
JS="function threewayFilter(kind){document.querySelectorAll('#threeway .threeway-card').forEach(el=>el.hidden=kind!=='all'&&el.dataset.kind!==kind)}"
