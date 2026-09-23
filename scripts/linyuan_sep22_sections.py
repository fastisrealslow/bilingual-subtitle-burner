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
    return latest_results()+latest_library()+title_trials()+followup_trials()+reference_speech()


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


def latest_results():
    path=RECORDS/'sep23-review.json'
    if not path.exists():return ''
    d=json.loads(path.read_text());r=d['trial9b']
    link=lambda run:'https://github.com/fastisrealslow/bilingual-subtitle-burner/actions/runs/'+str(run)
    body='<section id="latest-results"><h2>9月23日复核：数量改善，质量尚不能判优</h2>'
    body+='<p>同一固定100条：主线11自动出片／87拒绝／2未确定；最新整轮17／79／4。17份MP4均已核对哈希和完整解码，按现有去重规则仍是17份。编辑通过率尚未测定，目标30%未达成。</p>'
    body+='<p>严格同母片且两边完成判定的88对里：新增自动出片7、两边均出片10、两边均拒绝71。新增含误放行；另11条缺少可比母片哈希、1条运行未确定。主线有片的49号本轮缺报告，不算配对回退，也不能忽略其运行失败。</p>'
    body+='<p><a href="'+link(d['run_id'])+'">固定100完整运行</a> · <a href="../../linyuan/simulations/benchmark-20260921/sep23-review.json">逐条核查记录</a> · <a href="#threeway">三列实片与问题</a></p>'
    body+='<h3>单独9B Action的真实成片</h3><p>'+esc(r['note'])+'</p>'
    body+='<div class="review-player"><video controls playsinline preload="none" data-src="'+esc(r['file'])+'" poster="'+esc(r['poster'])+'"></video><button type="button">播放视频</button> <a href="'+esc(r['file'])+'" target="_blank" rel="noopener">单独打开视频</a> · <a href="'+esc(r['file'])+'" download>下载视频</a><p class="small" role="status" aria-live="polite"></p></div>'
    body+='<p>实际标题：'+esc(r['title'])+'<br>封面：'+esc(r['cover'])+'</p><p><a href="'+link(r['run_id'])+'">9B完整生产流程</a> · 固定代码 '+esc(r['tested_sha'][:7])+' · 20.89秒</p>'
    if d.get('trial9b_recheck'):
        row=d['trial9b_recheck']
        body+='<h3>第二次9B完整复验：仍有新的语义误放行</h3><p class="warning">'+esc(row['note'])+'</p><p>实际标题：'+esc(row['title'])+'<br>封面：'+esc(row['cover'])+'</p>'
        body+='<div class="review-player"><video controls playsinline preload="none" data-src="'+esc(row['file'])+'" poster="'+esc(row['poster'])+'"></video><button type="button">播放复验片</button> <a href="'+esc(row['file'])+'" target="_blank" rel="noopener">单独打开</a><p class="small" role="status" aria-live="polite"></p></div>'
        body+='<p><a href="'+link(row['run_id'])+'">实际运行</a>；文件完整解码通过，内容不因此通过。</p>'
    if d.get('title_trials'):
        body+='<h3>本轮8B／9B逐条文本结果</h3><div class="grid">'
        for row in d['title_trials']:
            body+='<article><h4>'+esc(row['case']+' · '+row['model'])+'</h4><p>'+esc(row['title'])+'</p><p>封面：'+esc(row['cover'])+'</p><p class="warning">'+esc(row['review_note'])+'</p><small>'+esc(row['status'])+' · '+esc(row['seconds'])+'秒；文本重放，不计出片</small></article>'
        body+='</div>'
    body+='<h3>这次继续修什么</h3><p>ff20e5a补上未研究＋转述的限定、独立标题对象与人口范围检查，覆盖拟稿、原话回退和旧证明复用；其后继续拦截没参与变退出、追加投入变零成本的错误；累计97项相关测试通过。拟稿优先具体个人选择，不强塞整段理由。8B／9B同3份原文重放与9B完整成片复验单列，结果不增加上面的固定100成绩。</p>'
    body+='<p><a href="'+link(d['current_trials']['text'])+'">新8B／9B对照</a> · <a href="'+link(d['current_trials']['video'])+'">新9B实片复验</a> · 当前状态：'+esc(d['current_trials_status'])+'</p>'
    body+='<p>画面问题卡恢复了54号的3段连续回答，但实际角标贴近头部、源画面缺少头顶余量，仍全部被拒绝。这次没有因此增加成片；也不降低现有画面标准换取出片率。</p>'
    if d.get('stage_trial',{}).get('file'):
        row=d['stage_trial']
        body+='<h3>舞台68完整复验：字幕改善，封面仍不合格</h3><p class="warning">'+esc(row['note'])+'</p>'
        body+='<p>实际标题：'+esc(row['title'])+'<br>实际封面：'+esc(row['cover'])+'</p>'
        body+='<div class="review-player"><video controls playsinline preload="none" data-src="'+esc(row['file'])+'" poster="'+esc(row['poster'])+'"></video><button type="button">播放完整复验片</button> <a href="'+esc(row['file'])+'" target="_blank" rel="noopener">单独打开</a><p class="small" role="status" aria-live="polite"></p></div>'
        body+='<p><a href="'+link(row['run_id'])+'">完整生产运行</a>；单条修复复验，不增加固定100成绩。新检查拒绝将方向判断写成不亏或保本。</p>'
    if d.get('source49_retry',{}).get('file'):
        row=d['source49_retry']
        body+='<h3>49号重试恢复：首次失败仍保留</h3><p>'+esc(row['note'])+'</p><p>实际标题：'+esc(row['title'])+'<br>封面：'+esc(row['cover'])+'</p>'
        body+='<div class="review-player"><video controls playsinline preload="none" data-src="'+esc(row['file'])+'" poster="'+esc(row['poster'])+'"></video><button type="button">播放重试恢复片</button> <a href="'+esc(row['file'])+'" target="_blank" rel="noopener">单独打开</a><p class="small" role="status" aria-live="polite"></p></div>'
    if d.get('stage_loss_trial'):
        row=d['stage_loss_trial']
        body+='<h3>舞台68再次复验：不亏已拦住，表达仍生硬</h3><p class="warning">'+esc(row['note'])+'</p><p>实际标题：'+esc(row['title'])+'<br>封面：'+esc(row['cover'])+'</p>'
        body+='<div class="review-player"><video controls playsinline preload="none" data-src="'+esc(row['file'])+'" poster="'+esc(row['poster'])+'"></video><button type="button">播放新标题实片</button> <a href="'+esc(row['file'])+'" target="_blank" rel="noopener">单独打开</a><p class="small" role="status" aria-live="polite"></p></div>'
    if d.get('publisher_trial'):
        row=d['publisher_trial']
        body+='<h3>34号背景残字取景修复</h3><p>'+esc(row['note'])+'</p><p><a href="'+link(row['run_id'])+'">实际复验运行</a> · '+esc(row['status'])+'</p>'
        if row.get('file'):
            body+='<div class="review-player"><video controls playsinline preload="none" data-src="'+esc(row['file'])+'" poster="'+esc(row['poster'])+'"></video><button type="button">播放取景复验片</button> <a href="'+esc(row['file'])+'" target="_blank" rel="noopener">单独打开</a><p class="small" role="status" aria-live="polite"></p></div>'

    body+='<h3>舞台字幕：修掉单字一屏的分组漏洞</h3><p>同一68号原文从37个识别碎片重新分为25屏，保留文字与时间依据，18项相关测试通过。下方仅重烧前30秒字幕作显示诊断，原标题仍不合格；不是新一轮自动出片、不计成功数。预览使用本机Arial Unicode MS字体，生产使用Noto Sans CJK SC。</p>'
    body+='<div class="review-player"><video controls playsinline preload="none" data-src="stage68-captions-sep23/caption-only-preview.mp4" poster="stage68-captions-sep23/frame.jpg"></video><button type="button">播放字幕诊断片</button> <a href="stage68-captions-sep23/caption-only-preview.mp4" target="_blank" rel="noopener">单独打开</a><p class="small" role="status" aria-live="polite"></p></div>'
    return body+'</section>'


def latest_library():
    path=RECORDS/'source-audit-sep23.json'
    if not path.exists():return ''
    d=json.loads(path.read_text())
    body='<section id="latest-library"><h2>9月23日最新素材库：发现记录不等于可用母片</h2>'
    body+='<p>线上快照 '+esc(d['snapshot_main_sha'][:7])+'：共 '+str(d['library_records'])+' 条记录，今天新发现 '+str(d['discovered_today'])+' 条；筛出 '+str(d['new_metadata_candidates_20s'])+' 条新发现、满足元数据准入条件的候选。已冻结全部17条另开完整出片验收；不拼进历史100，不把发现日期当录制日期。</p>'
    body+='<p>整库按120秒门槛有 '+str(d['admission']['120']['candidate_count'])+' 条可尝试，按20秒门槛有 '+str(d['admission']['20']['candidate_count'])+' 条；这里只通过元数据筛选，没有保证画面、身份、字幕或成片合格。主线生产代码与原对照基线相同，期间14个素材／运行状态文件更新。</p>'
    body+='<div class="table-scroll"><table><thead><tr><th>来源</th><th>固定100样本</th><th>首次自动出片</th><th>自动出片率</th></tr></thead><tbody>'
    for row in d['fixed100_platforms']:
        body+='<tr><td>'+esc(row['source'])+'</td><td>'+str(row['total'])+'</td><td>'+str(row['passed'])+'</td><td>'+str(row['percent'])+'%</td></tr>'
    body+='</tbody></table></div><p class="small">这些是固定样本的自动出片统计，不是全平台可用率或编辑通过率；小样本不能代表来源质量。新发现17条正在独立验收，阶段结果不能推算最终通过率。</p>'
    review=json.loads((RECORDS/'sep23-review.json').read_text())
    trial=review.get('fresh_library_trial',{})
    if trial:
        body+='<h3>新17条实际返回结果</h3><p>'+esc(str(trial.get('automatic_passes_at_update',0)))+' 条自动生成，'+esc(str(trial.get('rejected_at_update',0)))+' 条拒绝，'+esc(str(trial.get('unresolved_at_update',0)))+' 条运行未确定，'+esc(str(trial.get('pending_at_update',0)))+' 条尚未返回。'+esc(trial['note'])+'</p>'
        for row in trial.get('media',[]):
            body+='<article><h4>新增素材 '+esc(str(row['id']))+'</h4><p>'+esc(row['title'])+'<br>封面：'+esc(row['cover_title'])+'</p><p class="warning">'+esc(row['note'])+'</p>'
            body+='<div class="review-player"><video controls playsinline preload="none" data-src="'+esc(row['file'])+'" poster="'+esc(row['poster'])+'"></video><button type="button">播放新素材实际成片</button> <a href="'+esc(row['file'])+'" target="_blank" rel="noopener">单独打开</a><p class="small" role="status" aria-live="polite"></p></div></article>'
    for key,label in [('boundary_recovery_trial','选段修复复验'),('topic_split_trial','关税与人工智能分段复验'),
                      ('identity_gallery_trial','同源姿态核验后恢复312完整视频'),
                      ('spoken_focus_trial','先选观点的8B／9B标题实验'),('ordered_focus_trial','修正实际生成顺序后的复验'),
                      ('source_choices_trial','每个候选独立选择原文观点'),('publisher_source_trial','凤凰网新源实际验收'),
                      ('stage_border_trial','舞台裁边恢复与已有成片回归'),
                      ('short_title_trial','完整短句与数字限定的实际结果'),
                      ('final_short_title_trial','修复最终入口后的三条标题复验'),
                      ('channel_trial','311原声修复：听同一个片段'),
                      ('sep23_full100_trial','9月23日固定100条完整复验')]:
        row=review.get(key)
        if row:
            body+='<h3>'+label+'</h3><p>'+esc(row['note'])+'</p><p><a href="https://github.com/fastisrealslow/bilingual-subtitle-burner/actions/runs/'+esc(row['run_id'])+'">查看运行</a> · '+esc(row['status'])+'</p>'
            for media in row.get('media',[]):
                body+='<article><h4>素材 '+esc(media['id'])+' · 修复后的实际结果</h4><p>'+esc(media['title'])+'<br>封面：'+esc(media['cover_title'])+'</p><p class="warning">'+esc(media['note'])+'</p>'
                body+='<div class="review-player"><video controls playsinline preload="none" data-src="'+esc(media['file'])+'" poster="'+esc(media['poster'])+'"></video><button type="button">播放完整复验片</button> <a href="'+esc(media['file'])+'" target="_blank" rel="noopener">单独打开</a><p class="small" role="status" aria-live="polite"></p></div></article>'
            if row.get('rows'):
                body+='<div class="grid">'
                for result in row['rows']:
                    body+='<article><h4>'+esc(result['case'])+' · '+esc(result['model'])+'</h4><p>'+esc(result['title'])+'<br>封面：'+esc(result['cover'])+'</p><p class="warning">'+esc(result['review_note'])+'</p><p class="small">'+esc(result['seconds'])+'秒；纯文本实验，不计成片率。</p></article>'
                body+='</div>'
            if row.get('audio_pairs'):
                body+='<div class="grid">'
                for pair in row['audio_pairs']:
                    body+='<article><h4>原片 '+esc(pair['start'])+' 秒起 · 18秒对照</h4>'
                    for key,label in [('mono','原混合单声道'),('left','保留原始左声道')]:
                        clip=pair[key]
                        body+='<p>'+label+'</p><audio controls preload="none" src="'+esc(clip['file'])+'" style="width:100%"></audio><p class="small">机器转写：'+esc(clip['text'])+'</p>'
                    body+='</article>'
                body+='</div>'
    attribution=review.get('multi_guest_finding')
    if attribution:
        body+='<h3>素材8：人物在画面中，不代表这段原声属于他</h3><p class="warning">'+esc(attribution['note'])+'</p>'
    layout=review.get('cover_layout_trial')
    if layout:
        body+='<h3>308封面断句：保留完整谓语</h3><p>'+esc(layout['note'])+'</p><div class="grid">'
        for key,label in [('before','实际旧封面'),('after','新布局本地预览')]:
            body+='<figure><img loading="lazy" src="'+esc(layout[key])+'" alt="'+label+'" style="width:100%;height:auto"><figcaption>'+label+'</figcaption></figure>'
        body+='</div>'
    return body+'</section>'
