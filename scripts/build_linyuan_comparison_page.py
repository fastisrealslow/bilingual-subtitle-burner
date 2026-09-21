#!/usr/bin/env python3
"""Local paired viewing board; no publication or invented quality scores."""
from pathlib import Path
import html
import json
import os
from linyuan_overview_sections import sections as overview_sections, CSS as overview_css
from linyuan_threeway_review import build_threeway, CSS as threeway_css, JS as threeway_js

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'output/benchmark-20260921'
RECORDS = ROOT / 'linyuan/simulations/benchmark-20260921'
esc = html.escape


def read(path, default=None):
    return json.loads(path.read_text()) if path.exists() else default


def reference_media(bvid):
    folder = OUT / 'reference-complete' / ('reference20-' + bvid)
    proof = read(folder / 'inspection.json', {})
    if proof.get('complete_timeline'):
        return (str((folder/'full-review.mp4').relative_to(OUT)),
                str((folder/'frame-00.jpg').relative_to(OUT)),
                f"完整原声时间轴 · 实取 {proof['actual_dimensions'][0]}×{proof['actual_dimensions'][1]} · {proof['duration']:.1f}秒")
    partial = OUT / 'reference' / bvid
    proof = read(partial / 'inspection.json', {})
    if (partial/'preview-video-only.mp4').exists():
        return (str((partial/'preview-video-only.mp4').relative_to(OUT)),
                str((partial/'frame-0.jpg').relative_to(OUT)),
                f"局部画面参考 · 无音轨 · 已取得 {proof.get('decoded_duration',0):.1f}秒，未作全片听看")
    return None, str((partial/'cover.jpg').relative_to(OUT)), '完整媒体证据尚未取得'


def player(src, poster, label):
    media = (f'<video controls preload="none" src="{esc(src)}" poster="{esc(poster)}"></video>'
             if src else f'<img loading="lazy" src="{esc(poster)}">')
    return media + '<p class="small">' + esc(label) + '</p>'


def main():
    refs = read(RECORDS/'references-20.json', {'rows': []})['rows']
    by_bvid = {r['bvid']: r for r in refs}
    variants = {r['id']: r for r in read(OUT/'variants/manifest.json', [])}
    cards = []
    for row in read(RECORDS/'format-pair-review.json', []):
        key, bvid = row['ours'], row['reference_bvid']
        if key.startswith('story-'):
            aspect = key.split('-', 1)[1]
            ours = player('story/'+aspect+'.mp4', 'story/'+aspect+'.jpg', '29.68秒连续原声内容试剪，仍需听音验收')
        else:
            variant = variants[key]
            aspect = variant['aspect']
            ours = player('variants/'+key+'.mp4', 'variants/'+key+'.jpg', variant['label']+' · 12.2秒样式试片')
        source, poster, label = reference_media(bvid)
        ref_title = by_bvid.get(bvid, {}).get('title', '历史补充形式参考（不在正式20条组内）')
        cards.append(f'''<article class="pair" data-aspect="{aspect}">
<h3>{esc(key.upper())} · {esc(row['priority'])}</h3>
<div class="side"><div><b>我们的试样</b>{ours}</div><div><b>园园参考</b>{player(source,poster,label)}
<a href="https://www.bilibili.com/video/{bvid}" target="_blank">{esc(ref_title)}</a></div></div>
<p>{esc(row['comparison'])}</p><textarea data-note="{esc(key)}" placeholder="记录这个配对里更喜欢的部分（仅存本机）"></textarea></article>''')
    references = []
    complete = 0
    for r in refs:
        b = r['bvid']
        src, poster, label = reference_media(b)
        proof = read(OUT/'reference-complete'/('reference20-'+b)/'inspection.json', {})
        complete += bool(proof.get('complete_timeline'))
        note = r.get('full_stream_visual_observation') or r.get('observation') or '待完整证据到齐后补充逐条观察'
        references.append(f'''<article><span class="small">{esc(r['group'])} · {r['views']:,}次播放 · {r['date']}</span>
<h3><a href="{r['url']}">{esc(r['title'])}</a></h3>{player(src,poster,label)}
<p>{esc(note)}</p><small>平台原始尺寸：{r['dimensions']['width']}×{r['dimensions']['height']}；完整文件取到不代表已逐秒听看。</small></article>''')
    ab = read(ROOT/'output/baseline-comparison-20260921/results/comparison.json', {})
    status = read(ROOT/'output/baseline-comparison-20260921/run-status.json', {})
    stat_rows = []
    for name, row in ab.get('variants', {}).items():
        stat_rows.append(f"<tr><td>{esc(name)}</td><td>{row['passed']}</td><td>{row['rejected']}</td><td>{row['unresolved']}</td><td>{len(row['missing_ids'])}</td></tr>")
    stats = ('<table><tr><th>版本</th><th>报告通过</th><th>质量拒绝</th><th>未确定/待返回</th><th>尚缺报告</th></tr>'+''.join(stat_rows)+'</table>'
             if stat_rows else '<p>本地尚未汇入本批报告。</p>')
    stats += '<p class="small">仅统计本地已汇入的报告；运行期间数字不是最终成绩。自动通过不等于编辑合格。</p>'
    inventory=read(ROOT/'output/baseline-comparison-20260921/results/media-inventory-audit.json',{})
    for name,row in inventory.get('variants',{}).items():
        stats+='<p class="small">'+esc(name)+'：已完整解码 '+str(row['decoded_finals'])+' 份，按现有投稿去重规则保留 '+str(row['retained_by_publication_rule'])+' 份；这也不是人工质量通过数。</p>'
    cohorts=[]
    for label,path,run in (
        ('新版时长策略 · 固定100 · a6e50f7',ROOT/'output/reference100-20260921/results/local-summary.json','35557015452'),
        ('稳定性修复 · 固定100 · cc5113b',ROOT/'output/candidate100-35565180877/results/local-summary.json','35565180877'),
        ('素材库固定20 · 21f6d81',OUT/'library-35556200021/local-summary.json','35556200021')):
        row=read(path,{})
        if row:
            cohorts.append('<tr><td><a href="https://github.com/fastisrealslow/bilingual-subtitle-burner/actions/runs/'+run+'">'+esc(label)+'</a></td><td>'+str(row['passed'])+'/'+str(row['total'])+'</td><td>'+str(row['rejected'])+'</td><td>'+str(row['unresolved'])+'</td></tr>')
    if cohorts:
        stats+='<h3>其他固定批次，分开统计</h3><table><tr><th>批次 / 固定版本</th><th>报告通过 / 全部分母</th><th>拒绝</th><th>未确定 / 待返回</th></tr>'+''.join(cohorts)+'</table><p class="small">这些批次之后还有代码修复；不能把多轮最好结果合并为当前候选版的成功率。</p>'
    actual_cards = []
    media = read(ROOT/'output/baseline-comparison-20260921/results/media-verification.json', [])
    judgments = {r['id']: r for r in read(RECORDS/'production-pair-review.json', [])}
    for ident in sorted({r['id'] for r in media}):
        pair = [r for r in media if r['id'] == ident]
        judgment = judgments.get(ident, {})
        panels = []
        for variant in ('baseline', 'optimized'):
            row = next((r for r in pair if r['variant'] == variant), None)
            label = '主线' if variant == 'baseline' else '优化版（本批固定版本）'
            if row and row.get('video_audio_full_decode'):
                local = lambda key: os.path.relpath(ROOT/row[key], OUT)
                content = player(local('file'), local('cover'), '实际MP4已比对哈希并完整解码音视频')
                content += '<p>'+esc(row['title'])+'</p>'
                if row.get('current_title_gate_issue'):
                    content += '<p class="note">新增编辑检查：'+esc(row['current_title_gate_issue'])+'</p>'
            else:
                content = '<p>实际媒体尚未取得或核验未通过</p>'
            panels.append('<div><b>'+label+'</b>'+content+'</div>')
        bvid = judgment.get('reference_bvid')
        if bvid:
            panels.append('<div><b>园园对应形式</b>'+player(*reference_media(bvid))+
                          '<a href="https://www.bilibili.com/video/'+bvid+'">'+esc(by_bvid[bvid]['title'])+'</a></div>')
        actual_cards.append('<article class="pair"><h3>素材 '+str(ident)+'</h3><div class="grid">'+''.join(panels)+
                            '</div><p>'+esc(judgment.get('comparison', '待逐条补充编辑对照；不能按自动通过直接判优。'))+'</p></article>')
    actual_section = '<section id="actual"><h2>本批自动生成的实际成片</h2><p>主线、优化版和参考逐条摆在一起；标题问题及视觉缺点照实记录。播放器封面就是本条实际生成封面。</p>'+''.join(actual_cards)+'</section>'
    targeted=[]
    for row in read(RECORDS/'targeted-production-review.json',[]):
        directory=ROOT/row['media_directory']
        if not (directory/row['final']).is_file():continue
        ours=player(os.path.relpath(directory/row['final'],OUT),os.path.relpath(directory/row['cover'],OUT),
                    '定向实片 · 独立运行 '+str(row['run_id'])+' · 不替换固定100成绩')
        targeted.append('<article><h3>素材 '+str(row['id'])+' · '+esc(row.get('cohort','后续修复验证'))+'</h3><div class="side"><div>'+ours+
            '</div><div>'+player(*reference_media(row['reference_bvid']))+'</div></div><p>'+esc(row['comparison'])+'</p></article>')
    actual_section+='<section id="targeted"><h2>后续修复与素材库的真实成片</h2><p>短片、收尾和黑底版式单独验证；素材库20条实验与固定100分开。保留失败的文案与画面问题，不以产出文件代替质量验收。</p>'+''.join(targeted)+'</section>'
    stats = stats.replace('<table>', '<div class="table-scroll"><table>').replace('</table>', '</table></div>')
    state = '已结束，仍需核对媒体与编辑质量' if status.get('status') == 'completed' else '运行中，当前不是最终成绩'
    body = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>林园：固定100素材与20条参考对照</title>
<style>body{margin:0;background:#f0efe9;color:#172536;font:16px/1.65 system-ui}main{max-width:1320px;margin:40px auto;padding:0 22px}h1{font-size:38px;line-height:1.3}h2{margin-top:45px}h3{font-size:18px}.note{background:#dce8e1;border-left:4px solid #2b7268;padding:16px 22px}a{color:#206e78}nav{position:sticky;top:0;background:#f0efeff2;padding:12px 0;z-index:2;display:flex;gap:16px;flex-wrap:wrap}nav a,button{padding:8px 12px;border:1px solid #b8c8c4;background:white;border-radius:5px}section{scroll-margin-top:85px}.pair{margin:24px 0}.side{display:grid;grid-template-columns:1fr 1fr;gap:22px}.side>div{min-width:0}article{background:#fff;border-radius:8px;padding:18px;min-width:0}video{background:#080808;width:100%;height:370px;object-fit:contain}img{max-width:100%;max-height:370px;object-fit:contain}.small,small{color:#61717d;font-size:13px}.grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:20px}.grid video{height:300px}textarea{display:block;width:100%;box-sizing:border-box;min-height:65px;border:1px solid #ccd6d1;padding:9px}table{border-collapse:collapse;width:100%}td,th{padding:10px;border-bottom:1px solid #d5ded9;text-align:left}.hidden{display:none}@media(max-width:800px){.grid{grid-template-columns:1fr 1fr}video{height:290px}}@media(max-width:580px){.side,.grid{grid-template-columns:1fr}h1{font-size:29px}}</style>
<main><small>2026-09-21 · 主线/优化版 · 真实证据与尚待验证的部分分别标注</small><h1>先看能稳定做出多少，再看每条差在哪里</h1>
<nav><a href="#ab">固定100素材</a><a href="#actual">实际自动成片</a><a href="#pairs">18个试样逐项对照</a><a href="#refs">20条参考</a><a href="index.html">封面与模型实验</a></nav>
<section id="ab"><h2>同100条输入、两个固定版本</h2><p class="note">'''+state+'''。每个素材有一条合格MP4即止，测量可出片素材数，不把手工试样计入。两边分母均为100，未返回和失败不从分母删除；母片哈希不同不计配对胜负。报告通过后，还要独立核验媒体及内容。</p>'''+stats+'''<p><a href="https://github.com/fastisrealslow/bilingual-subtitle-burner/actions/runs/35548212133">查看本批Actions</a> · <a href="../../docs/LINYUAN_MAIN_COMPARISON_2026-09-21.md">比较规则与主线差异</a></p></section>
'''+actual_section+'''<section id="pairs"><h2>每个试样，都说清哪里接近、哪里还差</h2><p>16个12.2秒版式试样，加横竖两个约30秒完整经历试剪。它们与自动生产成片分开；“优先验证”是编辑判断，不是点击率胜出。参考账号的图像未用于我们的试片。</p>
<p><button onclick="filter('all')">全部</button> <button onclick="filter('portrait')">竖版</button> <button onclick="filter('landscape')">横版</button></p>'''+''.join(cards)+'''</section>
<section id="refs"><h2>20条参考：高播放与近期作品一起看</h2><p>12条历史高播放 + 8条近期短片，含9月20日新作。播放次数不是曝光点击率，也不直接比较新旧视频累计播放。当前取得完整原声时间轴 '''+str(complete)+'''/20；未完整取得的条目明确标注。下方平台标注的原始尺寸与实取视频尺寸分别展示。</p><div class="grid">'''+''.join(references)+'''</div></section>
<p><a href="../../linyuan/simulations/benchmark-20260921/illustration-prompt.md">原创插画：内置image_gen生成方式、完整提示词与保存路径</a></p></main>
<script>
function filter(aspect){document.querySelectorAll('#pairs .pair').forEach(el=>el.classList.toggle('hidden',aspect!=='all'&&el.dataset.aspect!==aspect))}
let notes={};try{notes=JSON.parse(localStorage.getItem('linyuan-pair-review-20260921')||'{}')}catch(e){}
document.querySelectorAll('textarea').forEach(t=>{t.value=notes[t.dataset.note]||'';t.oninput=()=>{notes[t.dataset.note]=t.value;try{localStorage.setItem('linyuan-pair-review-20260921',JSON.stringify(notes))}catch(e){}}});
document.querySelectorAll('video').forEach(v=>v.onplay=()=>document.querySelectorAll('video').forEach(o=>{if(o!==v)o.pause()}));
</script></html>'''
    body = body.replace('</style>', overview_css + '</style>')
    body = body.replace('</style>', threeway_css + '</style>')
    body = body.replace('</script>',threeway_js+'</script>')
    body = body.replace('<h1>先看能稳定做出多少，再看每条差在哪里</h1>', '')
    body = body.replace('<nav>', '<nav><a href="#threeway">三列看实片</a><a href="#overview">成功率总览</a><a href="#sources">素材来源</a><a href="#subtitles">字幕前后</a><a href="#packaging">标题封面</a><a href="#gap">园园差距</a>')
    overview=overview_sections()
    intro,separator,remaining=overview.partition('</section>')
    body = body.replace('</nav>', '</nav>'+intro+separator+build_threeway(reference_media,player)+remaining, 1)
    (OUT/'comparison.html').write_text(body)
    print(OUT/'comparison.html')


if __name__ == '__main__':
    main()
