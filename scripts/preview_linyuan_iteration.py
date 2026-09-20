#!/usr/bin/env python3
"""Reproducible local design preview; no model, network or publication calls."""
import argparse
import html
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'linyuan'))


def main():
    from PIL import Image
    from editorial_cover import render, font_path
    from production_diagnostics import summarize
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, default=ROOT/'output/optimization-v1')
    args=parser.parse_args()
    out=args.out
    out.mkdir(parents=True,exist_ok=True)
    old=ROOT/'linyuan/fc/reviewed_0910/cover-1.jpg'
    shutil.copy2(old,out/'previous-cover.jpg')
    # Existing reviewed cover's portrait: layout demonstration only. This crop
    # is never a production coordinate or a new source/identity certificate.
    portrait=Image.open(old).convert('RGB').crop((984,190,1236,484))
    headline='母亲用片仔癀，不敢乱给她吃'
    proof=render(portrait,out/'editorial-preview.jpg',(48,40,168,178),
                 headline,'林园',font_path())
    baseline=summarize(json.loads((ROOT/'site/production_status.json').read_text()),
                       '2026-09-18T00:00:00Z')
    (out/'baseline.json').write_text(json.dumps(baseline,ensure_ascii=False,indent=2))
    references=[]
    for path in sorted((out/'reference').glob('*.json')):
        row=json.loads(path.read_text())
        if row.get('owner',{}).get('name')!='园园滚雪球':continue
        references.append(f'<article><img src="reference/{html.escape(path.stem)}.jpg">'
            f'<p>{html.escape(row["title"])}</p><small>{row["duration"]} 秒 · '
            f'<a href="https://www.bilibili.com/video/{html.escape(row["bvid"])}">查看原视频</a></small></article>')
    completed=[]
    demo=out/'fixed-review/10608098788'
    if (demo/'meta.json').is_file():
        row=json.loads((demo/'meta.json').read_text())
        if isinstance(row,list):row=row[0]
        prefix='fixed-review/10608098788/'
        completed.append('<h2>实际完整样片</h2><p>'+html.escape(row['title'])+
            f' · {row["duration_sec"]} 秒。自动实产门禁通过，未投稿；下方播放前30秒。</p>'+
            '<div class="grid"><article><img src="'+prefix+'cover_16x9.jpg"><p>实际生成的备用文字封面</p></article>'+
            '<article><video controls preload="metadata" src="'+prefix+'preview_30s.mp4"></video>'+
            '<p><a href="'+prefix+'final.mp4">打开完整样片</a></p></article></div>')
    footer=out/'caption-footer-preview'
    if (footer/'preview-proof.json').is_file():
        completed.append('<h2>字幕遮脸：真实30秒对照</h2><p>同一原始母片、同一段字幕与时序。新版把字幕移到人物画面下方；此模式仍为实验选项。</p>'+
            '<div class="grid">'+''.join('<article><video controls preload="metadata" poster="caption-footer-preview/'+name+'.jpg" '+
                'src="caption-footer-preview/'+name+'.mp4"></video><p>'+label+'</p></article>'
                for name,label in [('before','原版：白底字幕覆盖嘴部'),('footer','实验版：字幕位于原画外，完整保留人物')])+'</div>')
    for directory,label in [('title-summary','第一轮：18次'),('title-guard-summary','困难案例复测：6次')]:
        summary_path=out/directory/'summary.json'
        if not summary_path.is_file():continue
        s=json.loads(summary_path.read_text())
        rows=[]
        for case in s['cases']:
            for trial in case['titles']:
                rows.append('<tr><td>'+html.escape(case['case'])+' / '+str(trial['repeat'])+'</td><td>'+
                    html.escape(trial.get('title') or trial.get('error') or '未完成')+'</td><td>'+
                    html.escape(trial.get('method') or trial.get('status') or '未知')+'</td></tr>')
        completed.append('<h2>真实标题验证 · '+label+'</h2>'+f'<p>完成{s["finished_trials"]}次；'+
            f'模型审核输出{s["model_reviewed"]}，原话兜底{s["quote_fallback"]}，未解决{s["unresolved"]}。</p>'+
            '<p class="note">这些是产出路径统计，不是质量通过率。逐条复核仍发现新增推论、事实改成建议、消费限定遗漏和原字幕歧义。'+
            '本版保留草稿，未合并生产。</p><details><summary>展开全部实际输出</summary><div class="table-scroll">'+
            '<table><thead><tr><th>案例 / 次数</th><th>实际标题（包含失败示例）</th><th>路径</th></tr></thead><tbody>'+
            ''.join(rows)+'</tbody></table></div></details>')
    page='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>林园出片 · 本地优化评审</title><style>
body{background:#f4f3ef;color:#192532;font:17px/1.7 system-ui;margin:0}main{max-width:1120px;margin:60px auto;padding:0 24px}
h1{font-size:40px;line-height:1.2}h2{margin-top:48px}p{max-width:850px}.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:24px}
article{background:white;padding:16px;border-radius:12px}img,video{width:100%;display:block}small{color:#596574}a{color:#246685}
table{border-collapse:collapse;width:100%;font-size:14px}th,td{padding:12px;border-bottom:1px solid #ccd2d3;text-align:left}.table-scroll{overflow:auto}summary{cursor:pointer}
.note{background:#e6eceb;padding:18px 24px;border-radius:8px}.thumb{width:160px;height:90px}.refs img{aspect-ratio:16/9;object-fit:contain;background:#18202a}
@media(max-width:650px){.grid{grid-template-columns:1fr}main{margin:30px auto}h1{font-size:30px}}
</style><main><small>LOCAL REVIEW / 2026-09-20—21 / 尚未上线</small><h1>林园出片：先看证据，再看观感</h1>
<p class="note">本页汇总完整样片、真实30秒字幕对照与封面排版演示，包含尚不合格的标题示例供复核。全部为测试产物，未投稿。</p>
'''+''.join(completed)+'''<h2>封面策略</h2><p>默认先尝试通过身份、清晰度和构图检查的现场原画。原画不适合横版封面时，才使用有完整人脸和两行大字的备用版式；不再按标题哈希轮换模板。</p>
<div class="grid"><article><img src="previous-cover.jpg"><p>历史版：同一条文案与肖像</p></article>
<article><img src="editorial-preview.jpg"><p>新版备用排版：统一层级，减少装饰与逐字变色</p><img class="thumb" src="editorial-preview_list_160.jpg"><small>160 × 90 实际列表尺寸</small></article></div>
<h2>失败基线</h2><p>9月18日 UTC 起的最近任务快照：52 个母片任务，49 个失败。人物 11、清晰度 11、取景 13、选段 10、运行故障 2、未分类 2。任务失败数不等于成片率，也不能据此推断新版效果。</p>
<h2>这版改变什么</h2><ul><li>一至三个有效标题候选都进入完整独立审核；不为凑足三个稿子丢掉好稿。</li>
<li>审核评分相同，再看原文中的具体对象、反差和说话方式；记录选稿依据。</li>
<li>素材调度参考近期已投稿或明确质检失败的不同母片；只微调排序，不剔除新来源。</li>
<li>按片记录耗时与失败类型，便于下一轮找出真正的瓶颈。</li></ul>
<h2>真实参考封面</h2><p>以下为只读对标资料，保留原账号标识，未用于我们的成片。公开视频元数据和图片来自 B站，不能由此推断点击率或全片质量。</p>
<div class="grid refs">'''+''.join(references)+'''</div><h2>验收边界</h2><p>本页区分排版演示、真实编码对照和自动门禁通过的完整样片。仍未完成全片人工听看，也未验证点击率或长期成片率。所有测试产物未投稿，时长门槛仍为120秒。</p></main></html>'''
    (out/'index.html').write_text(page,encoding='utf-8')
    print(out/'index.html')


if __name__=='__main__':main()
