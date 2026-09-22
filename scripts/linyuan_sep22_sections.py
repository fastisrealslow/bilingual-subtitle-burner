"""Latest title trials and reference speech analysis, with bounded claims."""
from pathlib import Path
import html
import json

ROOT=Path(__file__).resolve().parents[1]
RECORDS=ROOT/'linyuan/simulations/benchmark-20260921'
esc=lambda value:html.escape(str(value))


def title_trials():
    path=RECORDS/'title-sep22-results.json'
    if not path.exists():return ''
    data=json.loads(path.read_text())
    body='<section id="title-sep22"><h2>9月22日：独立模型到底改善了多少？</h2>'
    body+='<p>'+esc(data['conclusion'])+'</p><p class="small">同3条实片字幕 × 8B原提示 / 8B短提示 / 14B短提示。每组1次，不能据此声称模型稳定胜出；模型自己的通过判定不是编辑验收。</p>'
    body+='<p><a href="https://github.com/fastisrealslow/bilingual-subtitle-burner/actions/runs/'+str(data['run_id'])+'">查看9组实际运行</a></p>'
    for case in sorted({r['case'] for r in data['rows']}):
        body+='<h3>'+esc(case)+'</h3><div class="grid">'
        rows=sorted([r for r in data['rows'] if r['case']==case],key=lambda r:(r['model']=='qwen3:14b',r['draft_profile']=='concise'))
        for row in rows:
            label=row['model']+' · '+('原提示' if row['draft_profile']=='production' else '短提示')
            body+='<article><h4>'+esc(label)+'</h4><p>'+esc(row['title'])+'</p><p><strong>封面：</strong>'+esc(row['cover'])+'</p><p class="warning">'+esc(row['review_note'])+'</p><p class="small">'+esc(row['seconds'])+'秒；'+esc(row['method'])+'</p></article>'
        body+='</div>'
    body+='<p>本轮已落地：生意／买卖不再因分词词性误拦；生产提示只给本片事实；研究公司范围逐项保留；封面姓名、残句与目录式标题在独立复核前处理。新代码正在重复重放，结果与上表旧版本分开。</p>'
    followup=data.get('business_anchor')
    if followup:
        body+='<h3>修复误拦后，66发生了什么变化</h3><p>14B选中了原话支持的完整判断，另两条加推断的候选被拒绝；8B仍有补写反差或封面残句。这是单样本重放，未增加出片率，也不是整批质量验收。</p><details><summary>展开3组实际结果</summary>'
        for row in followup['rows']:
            body+='<p><strong>'+esc(row['model']+' / '+row['profile'])+'</strong>：'+esc(row['title'])+'<br>封面：'+esc(row['cover'])+'</p>'
        body+='</details><p><a href="https://github.com/fastisrealslow/bilingual-subtitle-burner/actions/runs/'+str(followup['run_id'])+'">查看修复后的独立运行</a></p>'
    body+='</section>'
    return body


def reference_speech():
    path=RECORDS/'reference-speech-review.json'
    if not path.exists():return ''
    data=json.loads(path.read_text())
    body='<section id="reference-speech"><h2>园园的吸引力：从完整讲话看选段与标题</h2><p>'+esc(data['scope'])+'</p>'
    body+='<p>'+esc(data['conclusion'])+'</p><div class="table-scroll"><table><thead><tr><th>参考</th><th>值得学的内容选择</th><th>我们怎样改</th><th>仍需注意</th></tr></thead><tbody>'
    for row in data['rows']:
        body+='<tr><td><a href="https://www.bilibili.com/video/'+esc(row['bvid'])+'" target="_blank" rel="noopener">'+esc(row['topic'])+'</a><br>'+esc(row['duration'])+'秒 · '+esc(row['group'])+'</td><td>'+esc(row['observation'])+'</td><td>'+esc(row['action'])+'</td><td>'+esc(row['limit'])+'</td></tr>'
    return body+'</tbody></table></div><p class="small">下方20条参考区保留实际视频。机器转写用于定位与分析，不能代替逐句听音；存在转写疑点时不修改原字幕、不把猜测写成事实。</p></section>'


def sections():
    return title_trials()+followup_trials()+reference_speech()


def followup_trials():
    path=RECORDS/'sep22-followup-results.json'
    if not path.exists():return ''
    data=json.loads(path.read_text())
    link=lambda run:'https://github.com/fastisrealslow/bilingual-subtitle-burner/actions/runs/'+str(run)
    body='<section id="title-followup"><h2>最新实测：9B、重复稳定性与实际失败视频</h2>'
    body+='<p>独立Action已经运行。9B在66号保住了生意原意，但95号改动统计对象、遗漏时间范围；8B同设置重复运行也会选出不同且有问题的标题。暂不切换线上默认模型。</p>'
    for key,label in [('title9b','同3份字幕：8B与9B短提示'),('cover_scope','修复后：95号研究公司范围'),('repeat8b','同设置重复：8B输出是否稳定')]:
        batch=data[key]
        body+='<h3>'+label+'</h3><p><a href="'+link(batch['run_id'])+'">查看这批实际Action</a></p><div class="grid">'
        for row in sorted(batch['rows'],key=lambda r:(r['case'],r['model'],r['repeat'])):
            body+='<article><h4>'+esc(row['case']+' · '+row['model']+' · 第'+str(row['repeat'])+'次')+'</h4><p>'+esc(row['title'])+'</p><p><strong>封面：</strong>'+esc(row['cover'])+'</p><p class="warning">'+esc(row['review_note'])+'</p><small>'+esc(row['status'])+' · '+esc(row['seconds'])+'秒 · '+esc(row['commit'][:7])+'</small></article>'
        body+='</div>'
    body+='<p class="small">以上均为文案重放，不是新增成片或发布验收；时间只含单条文本生成和复核，未包含下载模型、排队或渲染。</p>'
    row=data['source17']
    body+='<h3>17号：能生成MP4，标题依然不能交付</h3><p class="warning">'+esc(row['review_note'])+'</p>'
    body+='<div class="review-player"><video controls playsinline preload="none" data-src="'+esc(row['file'])+'" poster="'+esc(row['poster'])+'"></video><button type="button">播放视频</button> <a href="'+esc(row['file'])+'" target="_blank" rel="noopener">单独打开视频</a> · <a href="'+esc(row['file'])+'" download>下载视频</a><p class="small" role="status" aria-live="polite"></p></div>'
    body+='<p>实际标题：'+esc(row['title'])+'<br>实际封面：'+esc(row['cover'])+'</p><p class="small">'+esc(row['duration'])+'秒 · 代码'+esc(row['tested_sha'][:7])+' · <a href="'+link(row['run_id'])+'">实片运行</a>；单条诊断，不加入固定100的历史成绩。</p>'
    body+='<h3>模型选段：建议仍需核实前后文</h3><p>两份长素材分别交8B、9B提出连续片段。8B有一段停在“为什么”，却漏掉后面的回答；9B也会在理由中描述实际选段外的内容。候选理由写得完整，不代表剪辑完整。</p><p><a href="'+link(data['selection']['run_id'])+'">查看4组原始选段实验</a>；尚未接入生产选段，也未增加成片数。</p>'
    batch=data['full100']
    body+='<h3>修复版整批100条复验</h3><p>'+esc(batch['note'])+'</p><p><a href="'+link(batch['run_id'])+'">查看固定100进度</a> · 固定代码 '+esc(batch['tested_sha'][:7])+ '</p>'
    return body+'</section>'
