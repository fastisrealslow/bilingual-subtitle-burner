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
    page='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>林园出片 · 本地优化评审</title><style>
body{background:#f4f3ef;color:#192532;font:17px/1.7 system-ui;margin:0}main{max-width:1120px;margin:60px auto;padding:0 24px}
h1{font-size:40px;line-height:1.2}h2{margin-top:48px}p{max-width:850px}.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:24px}
article{background:white;padding:16px;border-radius:12px}img{width:100%;display:block}small{color:#596574}a{color:#246685}
.note{background:#e6eceb;padding:18px 24px;border-radius:8px}.thumb{width:160px;height:90px}.refs img{aspect-ratio:16/9;object-fit:contain;background:#18202a}
@media(max-width:650px){.grid{grid-template-columns:1fr}main{margin:30px auto}h1{font-size:30px}}
</style><main><small>LOCAL REVIEW / 2026-09-20 / 尚未上线</small><h1>林园出片：先看证据，再看观感</h1>
<p class="note">本页是静态评审，不调用模型、不投稿。文字封面使用工程已有肖像，同文案比较排版；它不是新生成的合格成片。</p>
<h2>封面策略</h2><p>默认先尝试通过身份、清晰度和构图检查的现场原画。原画不适合横版封面时，才使用有完整人脸和两行大字的备用版式；不再按标题哈希轮换模板。</p>
<div class="grid"><article><img src="previous-cover.jpg"><p>历史版：同一条文案与肖像</p></article>
<article><img src="editorial-preview.jpg"><p>新版备用排版：统一层级，减少装饰与逐字变色</p><img class="thumb" src="editorial-preview_list_160.jpg"><small>160 × 90 实际列表尺寸</small></article></div>
<h2>失败基线</h2><p>9月18日 UTC 起的最近任务快照：52 个母片任务，49 个失败。人物 11、清晰度 11、取景 13、选段 10、运行故障 2、未分类 2。任务失败数不等于成片率，也不能据此推断新版效果。</p>
<h2>这版改变什么</h2><ul><li>一至三个有效标题候选都进入完整独立审核；不为凑足三个稿子丢掉好稿。</li>
<li>审核评分相同，再看原文中的具体对象、反差和说话方式；记录选稿依据。</li>
<li>素材调度参考近期已投稿或明确质检失败的不同母片；只微调排序，不剔除新来源。</li>
<li>按片记录耗时与失败类型，便于下一轮找出真正的瓶颈。</li></ul>
<h2>真实参考封面</h2><p>以下为只读对标资料，保留原账号标识，未用于我们的成片。公开视频元数据和图片来自 B站，不能由此推断点击率或全片质量。</p>
<div class="grid refs">'''+''.join(references)+'''</div><h2>仍需实产验证</h2><p>新一轮模型重复生成、完整视频与音频、字幕时序、B站发布回执均不在此预览的验证范围。时长门槛当前仍为120秒。</p></main></html>'''
    (out/'index.html').write_text(page,encoding='utf-8')
    print(out/'index.html')


if __name__=='__main__':main()
