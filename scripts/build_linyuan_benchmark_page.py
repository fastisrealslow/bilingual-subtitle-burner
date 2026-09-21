#!/usr/bin/env python3
"""Build a local comparison board from fetched evidence and review-only media."""
from pathlib import Path
import html
import json
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'output/benchmark-20260921'
esc=html.escape

def read(path,default):return json.loads(path.read_text()) if path.exists() else default

def main():
    data=read(OUT/'benchmark.json',{'rows':[]});refs=[]
    for r in data['rows']:
        b=r['bvid'];frames=r['inspection']['frame_times'];duration=r['inspection']['decoded_duration']
        refs.append(f'''<article class="reference" data-aspect="{r['aspect']}"><div class="badge">{esc(r['group'])} · {r['views']:,} 次播放 · {r['duration']} 秒</div>
<a href="{r['url']}" target="_blank"><img loading="lazy" class="cover" src="reference/{b}/cover.jpg"></a>
<h3>{esc(r['title'])}</h3><p>{esc(r['observed_format'])} · {r['dimensions']['width']}×{r['dimensions']['height']}</p>
<p>{esc(r['observation'])}</p><details><summary>查看实际画面（{len(frames)}帧）</summary><div class="frames">'''+''.join(f'<img loading="lazy" src="reference/{b}/frame-{i}.jpg" title="{t:.1f}秒">' for i,t in enumerate(frames))+f'''</div><small>取得视频流 {duration:.1f} 秒；抽帧检查，未全片听看。</small></details>
<a href="{r['url']}" target="_blank">打开原视频</a></article>''')
    trials=read(OUT/'variants/manifest.json',[]);variants=[]
    for r in trials:
        k=r['id'];variants.append(f'''<article class="variant" data-aspect="{r['aspect']}"><div class="badge">{k.upper()} · {esc(r['label'])} · {r['dimensions'][0]}×{r['dimensions'][1]}</div>
<video controls preload="none" poster="variants/{k}.jpg" src="variants/{k}.mp4"></video>
<h3>{esc(r['title'])}</h3><p>同一段 {r['duration']:.1f} 秒原音频；这是排版试样，不是新库存。</p>
<button class="pick" data-id="{k}">☆ 留作候选</button><textarea data-note="{k}" placeholder="记下喜欢或需要改的地方（仅保存在本机浏览器）"></textarea></article>''')
    models=[]
    for p in sorted((OUT/'model-results').rglob('qwen*-case-*.json')):
        row=read(p,{})
        if not row:continue
        verdict={v['index']:v for v in row.get('reviews',[])}
        lines=[]
        for i,c in enumerate(row.get('candidates',[])):
            v=verdict.get(i,{});flags=[k for k in ('source_supported','speaker_correct','qualifiers_preserved','natural','distinctive') if v.get(k) is False]
            lines.append('<tr><td>'+esc(c.get('angle',''))+'</td><td>'+esc(c.get('title',''))+'</td><td>'+esc(c.get('cover',''))+'</td><td>'+esc(c.get('hook_quote',''))+'</td><td>'+esc('、'.join(c.get('binding_errors',[])+flags) or '机器未标错，仍需人工看')+'</td></tr>')
        models.append(f'<details><summary>{esc(row["profile"])} · {esc(row["case"])} · {row.get("seconds",0):.1f}秒 · {esc(row["status"])}</summary><div class="scroll"><table><tr><th>角度</th><th>实际生成标题</th><th>实际封面文案</th><th>原文锚点</th><th>检查结果</th></tr>'+''.join(lines)+'</table></div><p>'+esc(row.get('error',''))+'</p></details>')
    covers=[]
    for i,label in enumerate(['干净真人近景','近景加短句','留白与人物分栏','现场图加底部观点']):
        if (OUT/f'variants/cover-{i}.jpg').exists():covers.append(f'<article><img loading="lazy" src="variants/cover-{i}.jpg"><h3>{label}</h3><img class="thumb" src="variants/cover-{i}-160.jpg"><small>160×90 列表尺寸</small></article>')
    stories=[]
    for r in read(OUT/'story/manifest.json',[]):
        k=r['aspect'];stories.append(f'<article><div class="badge">{esc(k)} · 29.68秒原声经历</div><video controls preload="none" poster="story/{k}.jpg" src="story/{k}.mp4"></video><h3>{esc(r["title"])}</h3></article>')
    drafts=read(ROOT/'linyuan/simulations/benchmark-20260921/editorial-drafts.json',[])
    draft_table=''.join('<tr><td>'+esc(r['angle'])+'</td><td>'+esc(r['title'])+'</td><td>'+esc(r['cover'])+'</td><td><details><summary>对应字幕</summary>'+esc(r['source_quote'])+'</details></td></tr>' for r in drafts)
    failures=read(ROOT/'linyuan/simulations/benchmark-20260921/actions-review.json',[])
    error_table=''.join(f'<tr><td><a href="{r["url"]}">{r["id"]}</a></td><td>{esc(r["reason"])}</td><td>{esc(r["implication"])}</td></tr>' for r in failures)
    body='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>林园 × 园园 · 16种样式实验</title>
<style>body{margin:0;background:#f1efe9;color:#152333;font:16px/1.65 system-ui}main{max-width:1300px;margin:45px auto;padding:0 24px}h1{font-size:42px;line-height:1.2}h2{margin-top:48px}h3{font-size:18px}p{max-width:1000px}a{color:#186e79}nav{position:sticky;top:0;z-index:2;background:#f1efe9ef;padding:14px 0;display:flex;gap:12px;flex-wrap:wrap}nav a,button{border:1px solid #abc1c0;background:#fff;padding:8px 15px;border-radius:6px;cursor:pointer}section{scroll-margin-top:80px}.grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:18px}.reference-grid{grid-template-columns:repeat(3,minmax(0,1fr))}article{background:white;padding:14px;border-radius:9px;min-width:0}article img{width:100%;display:block}article video{display:block;background:#080808;width:100%;max-height:500px}article[data-aspect=portrait] video{aspect-ratio:9/16}.cover{aspect-ratio:16/9;object-fit:contain;background:#ddd}.badge,small{color:#61717d;font-size:12px}.note{padding:18px 22px;background:#dfe9e5;border-left:4px solid #377567}table{border-collapse:collapse;width:100%;font-size:14px}td,th{padding:12px;border-bottom:1px solid #d8deda;text-align:left;vertical-align:top}.scroll{overflow:auto}.frames{display:flex;gap:5px}.frames img{width:32%;object-fit:contain}.thumb{width:160px!important;height:90px}textarea{display:block;box-sizing:border-box;width:100%;margin-top:12px;min-height:60px;border:1px solid #d3dcd9;padding:8px}button.chosen{background:#ffe59f}details{margin:15px 0}summary{cursor:pointer}.hidden{display:none}@media(max-width:1000px){.grid,.reference-grid{grid-template-columns:repeat(2,minmax(0,1fr))}}@media(max-width:600px){.grid,.reference-grid{grid-template-columns:1fr}h1{font-size:30px}main{padding:0 14px}}</style>
<main><small>2026-09-21 · 原始证据 / 对照实验 / 尚未发布</small><h1>横版和竖版，都有自己的做法</h1>
<p class="note">16条真实参考：历史高播放8条、近期6条、特殊形式2条。我们另外制作横竖各8个有原声的12.2秒排版试样和4种封面。参考账号素材只用于对照，我们的试样来自已有母片。播放次数不是点击率，也不能把老视频与刚发布的视频直接按播放数比较优劣。</p>
<nav><a href="#variants">16个试样</a><a href="#covers">4种封面</a><a href="#reference">16条参考</a><a href="#content">选段与标题</a><a href="#models">模型实测</a><a href="#errors">近期错误</a></nav>
<section id="variants"><h2>先看我们的横竖版试样</h2><p>同一原声观点，改变画面结构、人物大小、文字位置和封面。短片用于选样式；固定裁切坐标只适用于这段素材，没有写成通用生产规则。P07另用项目身份参考生成原创人物插画，画面明确标注AI插画；未采用参考账号的插画。</p>
<p><button onclick="filter('variant','all')">全部</button> <button onclick="filter('variant','portrait')">竖版 8 个</button> <button onclick="filter('variant','landscape')">横版 8 个</button></p><div class="grid">'''+''.join(variants)+'''</div></section>
<section id="covers"><h2>封面不必等于视频顶部标题</h2><p>参考的真人视频多数使用干净近景封面；竖屏播放时才显示顶部观点条。这里分开比较封面、投稿标题、片内常驻文字。</p><div class="grid">'''+''.join(covers)+'''</div></section>
<section id="reference"><h2>参考作品：看画面，也看原话的力度</h2><p>历史高播放组6竖2横；近期所选6条均为横版画布，其中婚礼视频是竖拍置入横版。另有插画音频卡和活动拼图。这个样本用于研究做法，不代表整个账号的比例。</p><p><button onclick="filter('reference','all')">全部</button> <button onclick="filter('reference','portrait')">竖版</button> <button onclick="filter('reference','landscape')">横版画布</button></p><div class="grid reference-grid">'''+''.join(refs)+'''</div></section>
<section id="content"><h2>先选最值得听的一段，再给它一个标题</h2><div class="grid">'''+''.join(stories)+'''</div><p>新增约30秒内容试剪：保留完整原声、结尾“当然我也希望他把钱给我”，没有剪成“不要房租”。字幕合并断句并省去口头填充，一处歧义转写暂留空待听音；仍是试剪，未投稿。</p><p>我们上一版267.7秒“医药股”片段，开头约12秒谈医药经营，后面转到自我安慰、房租、管理规模、车厂调研。标题再好，也不能让这些话题自动变成一条集中内容。下面是同一母片可拆出的编辑草案，均需进一步听音验收。</p>
<table><tr><th>原片区间</th><th>独立内容</th><th>可尝试的标题草案</th></tr>
<tr><td>2489.48–2501.64</td><td>医药公司也有经营不好的</td><td>林园：医药股也有经营不好的，别乱买</td></tr>
<tr><td>2584.36–2614.04</td><td>被拖欠半年房租，先让租客腾房</td><td>林园：半年没付我房租，我先让他把房子腾回来</td></tr>
<tr><td>2696.76–2735.16</td><td>回应车厂调研，解释只是跟朋友转一圈</td><td>林园：我就跟朋友去车厂转一圈，没太多精力放上面</td></tr></table><p>后两种更有个人经历、具体对象或反差；有力表达来自原话。不要为了“雷霆”给他补一句没说过的话。16条参考里6条不足120秒，当前120秒硬门槛也值得另开短观点实验。</p></section>
<h3>7个编辑标题草案：把林园原话里的态度留下来</h3><p>以下是依据实际字幕写出的编辑草案，与上面的布局试样、下面的模型原始输出分别标注；尚待听音核对。“我”“没准”等限定保留，封面不把个人经历改成普遍承诺。</p><div class="scroll"><table><tr><th>切入点</th><th>标题草案</th><th>封面草案</th><th>来源</th></tr>'''+draft_table+'''</table></div>
<section id="models"><h2>8B / 9B / 9B思考：同原文对比</h2><p class="note">15项实验全部收齐：完整片段的8B、9B非思考各完成4/4项，但均有语义错误；9B思考4/4超过20分钟请求上限。单观点中8B用222秒、9B非思考173秒，9B思考685秒后耗尽输出预算，没有完整结果。本轮不替换生产默认模型。</p><p>4组实际字幕 × 3种模型配置 × 6种表达角度，最多72个候选。另加一组只含29.68秒房租经历的实验，允许1–3个候选，不强行凑满6种。两组输入不同，应分别比较。每项有独立生成和审核请求，记录耗时、引用、缺失和失败。机器通过不能替代逐条编辑验收；未自动写入生产标题。</p><p><a href="https://github.com/fastisrealslow/bilingual-subtitle-burner/actions/runs/35545423737">查看完整片段实验</a> · <a href="https://github.com/fastisrealslow/bilingual-subtitle-burner/actions/runs/35546700511">查看单观点实验</a></p>'''+(''.join(models) or '<p>本地尚未汇入模型结果。</p>')+'''</section>
<section id="errors"><h2>最近Actions到底卡在哪里</h2><div class="scroll"><table><tr><th>运行</th><th>实际错误</th><th>处理方向</th></tr>'''+error_table+'''</table></div><p>已修复：队首需要大陆中转时，后续可直接交给GitHub的来源不再被一起挡住；账户欠费单独记入失败回执，不算素材劣质。账户本身尚未恢复，改动尚未部署。</p></section>
<p><a href="../../docs/LINYUAN_BENCHMARK_2026-09-21.md">查看完整分析与实现说明</a></p></main>
<script>
function filter(cls,aspect){document.querySelectorAll('.'+cls).forEach(el=>el.classList.toggle('hidden',aspect!=='all'&&el.dataset.aspect!==aspect))}
let saved={};try{saved=JSON.parse(localStorage.getItem('linyuan-layout-review')||'{}')}catch(e){}
function save(){try{localStorage.setItem('linyuan-layout-review',JSON.stringify(saved))}catch(e){}}
document.querySelectorAll('.pick').forEach(b=>{function paint(){b.classList.toggle('chosen',!!saved[b.dataset.id]?.picked);b.textContent=saved[b.dataset.id]?.picked?'★ 已留作候选':'☆ 留作候选'}paint();b.onclick=()=>{saved[b.dataset.id]??={};saved[b.dataset.id].picked=!saved[b.dataset.id].picked;save();paint()}})
document.querySelectorAll('textarea').forEach(t=>{t.value=saved[t.dataset.note]?.note||'';t.oninput=()=>{saved[t.dataset.note]??={};saved[t.dataset.note].note=t.value;save()}})
document.querySelectorAll('video').forEach(v=>v.onplay=()=>document.querySelectorAll('video').forEach(o=>{if(o!==v)o.pause()}));
</script></html>'''
    (OUT/'index.html').write_text(body,encoding='utf-8');print(OUT/'index.html')

if __name__=='__main__':main()
