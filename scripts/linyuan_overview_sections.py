"""Evidence-backed overview sections for the local comparison page."""
from pathlib import Path
import html
import json
import os
import re

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'output/benchmark-20260921'
esc = lambda s: html.escape(str(s))


def read(path, default=None):
    return json.loads(path.read_text()) if path.exists() else default


def table(headers, rows):
    return '<div class="table-scroll"><table><thead><tr>' + ''.join('<th>'+esc(h)+'</th>' for h in headers) + '</tr></thead><tbody>' + ''.join('<tr>'+''.join('<td>'+esc(c)+'</td>' for c in r)+'</tr>' for r in rows) + '</tbody></table></div>'


def asset(path):
    return esc(os.path.relpath(ROOT / path, OUT))


def subtitle_panel(row, label):
    if not row or not row.get('file'):
        return '<div><h3>'+esc(label)+'</h3><p>本批没有实际成片。</p></div>'
    folder = (ROOT / row['file']).parent
    paths = sorted(folder.glob('subtitles*.ass'))
    lines = []
    if paths:
        for line in paths[0].read_text().splitlines():
            if line.startswith('Dialogue:'):
                fields = line.split(',', 9)
                if len(fields) == 10:
                    text = re.sub(r'\{[^}]*\}', '', fields[9]).replace(r'\N', ' / ')
                    lines.append(fields[1]+' → '+fields[2]+'  '+text)
    contact = next(folder.glob('contact_sheet*.jpg'), None)
    photo = '<img loading="lazy" src="'+asset(contact)+'" alt="'+esc(label)+'实际六帧字幕与取景">' if contact else ''
    return '<div><h3>'+esc(label)+'</h3>'+photo+'<p>'+esc(row['title'])+'</p><details><summary>展开实际字幕全文与时间轴（'+str(len(lines))+'条）</summary><pre class="transcript">'+esc('\n'.join(lines))+'</pre></details></div>'


def sections():
    d = read(OUT/'source-audit/yield-audit.json', {})
    if not d:
        return '<p>来源统计尚未生成，请先运行 scripts/audit_linyuan_source_yield.py。</p>'
    labels = {'baseline':'原主线 · 同批100', 'optimized':'早期优化版 · 同批100',
              'reference100':'新时长策略 · 固定100', 'library20':'当前素材库 · 固定20',
              'candidate100':'稳定性修复 · 固定100（cc5113b）'}
    batchrows = []
    charts = []
    groups = []
    for key, value in d['cohorts'].items():
        n = value['total']; good = value['passed']; bad = value['rejected']; unknown = value['unresolved']
        batchrows.append([labels[key], value['tested_shas'][0][:7] if value['tested_shas'] else '未知',
            f'{good}/{n} = {100*good/n:g}%', bad, f'{unknown}（缺报告{value["missing"]}）'])
        charts.append('<div class="bar-row"><b>'+esc(labels[key])+'</b><div class="bar" aria-label="'+esc(f'通过{good}，拒绝{bad}，未确定{unknown}')+'">'+ \
            f'<span class="good" style="width:{100*good/n}%">{good}</span><span class="bad" style="width:{100*bad/n}%">{bad}</span><span class="pending" style="width:{100*unknown/n}%">{unknown}</span></div></div>')
        def group_rows(field):
            return [[r['source'],r['total'],r['downloaded'],r['source_gate_passed'],f'{r["passed"]}/{r["total"]}（{r["percent"]}%）',r['rejected'],r['unresolved']] for r in value[field]]
        headers=['来源','输入数','已取到母片¹','源检查通过','自动出片 / 输入','拒绝','未确定']
        groups.append('<div class="source-cohort" data-cohort="'+key+'"'+(' hidden' if key!='library20' else '')+'><h3>'+esc(labels[key])+'</h3>'+table(headers,group_rows('platforms'))+ \
            '<details><summary>展开按上传作者统计（作者不是原始拍摄方）</summary>'+table(headers,group_rows('authors'))+'</details></div>')
    intro = '<section id="overview"><div class="eyebrow">整体验收 / 2026-09-21</div><h1>形式更接近了，稳定性还没达标。</h1><p class="lead">目标是每100条素材至少30条能稳定出片，并且内容值得发布。目前还不能交出这个结论。</p><p class="small">本地报告快照：'+esc(d['checked_at'])+'；主线数据 '+d['snapshot_main_sha'][:7]+'。页面不会自动更新；不同固定版本分别统计。</p>'+ \
        '<div class="metric-grid"><article><b>11%</b><span>主线 · 固定100自动出片</span></article><article><b>'+str(d['cohorts']['candidate100']['passed'])+'%</b><span>cc5113b · 同批100自动出片</span></article><article><b>'+str(d['cohorts']['library20']['passed'])+'/20</b><span>素材库抽样 · 自动出片</span></article><article><b>30%</b><span>目标 · 尚未达到</span></article></div>'+''.join(charts)+ \
        '<p class="small">绿色＝自动通过；红色＝质量拒绝；灰色＝未确定，含运行失败与尚缺报告。颜色不表示人工编辑质量。</p>'+table(['批次','固定代码','自动出片率','拒绝','未确定'],batchrows)+ \
        '<p class="warning">线上历史真实出片率仍不可精确还原：“任务完成”不等于“产出合格视频”。上表11%是主线代码在固定100素材上的实测，不冒充线上长期统计；10%是早期优化版本。cc5113b整轮已结束，仍有运行未确定项；更晚的标题修复尚未完成整批验收，具体代码与模型实验见下方；不能拼接多轮最好结果声称达标。</p>'+ \
        '<p>旧100对照已收齐两边各100份报告。主线11份、早期优化10份实际MP4已完整解码；按当前投稿去重规则分别保留10份和9份。素材58两边下载字节不同，不计同母片胜负；素材95是一次自动出片回退。所有自动通过结果仍需内容验收，尚无经完整听音确认的编辑通过率。</p></section>'
    admission=d['admission']
    sources='<section id="sources"><h2>素材：有更新，但大库不等于可用库存</h2><p>冻结主线f794ce7素材库收录 '+str(d['library_records'])+' 条记录。北京时间9月21日新增 '+str(d['discovered_today'])+' 条：B站搜索36、来源定向搜索4、微博7、网易1、园园参考2。48条候选来源加2条参考，不能说成新增50条可用母片。</p>'+ \
        '<p>今早 <a href="https://github.com/fastisrealslow/bilingual-subtitle-burner/actions/runs/'+esc(d['research_run'])+'">06:28–06:36 的监控</a>确实执行了抓取。来源研究累计74个母片下载任务，54个已完成媒体检查、17个待重试、3个待处理；参考视频另算22个，其中17个已检查。此次3个媒体任务是1个抖音母片失败、1个参考片成功、1个参考片失败，所以不能宣称“今早已下载一批新的合格母片”。</p>'+ \
        table(['同一5996条库 / 同一发布状态','元信息可进入后续处理','说明'],[['旧120秒门槛',admission['120']['candidate_count'],'源端先排除了大量完整短观点'],['新20秒下限 + 完整内容策略',admission['20']['candidate_count'],'候选增多，仍须身份、画面、选段与文案验收']])+ \
        '<p>此前固定20是从当时73条候选中抽取；本次刷新重算得到76条，原20条实验的抽样与分母保持不变。素材214的AI行业/公司选择、218的泡沫观点、220的运气故事已出实片；216下载修复后与220内容重复，不能增加一条独立库存。</p>'+ \
        '<label>选择统计批次 <select id="source-cohort" onchange="document.querySelectorAll(\'.source-cohort\').forEach(x=>x.hidden=x.dataset.cohort!==this.value)">'+''.join('<option value="'+k+'"'+(' selected' if k=='library20' else '')+'>'+esc(v)+'</option>' for k,v in labels.items())+'</select></label>'+''.join(groups)+ \
        '<p class="small">¹ 包括下载步骤成功，或缓存复用且已有实际母片SHA；不等于编辑可用。未完成报告留在原分母。作者样本多为1条，不据此宣称某作者100%可靠；平台差异还受素材长度、搬运裁切和画质影响。</p>'+ \
        '<h3>新的检索与来源整理</h3><p>本轮额外刷新搜索20条结果，没有找到库外的非参考母片；新发现园园9月20日的两条作品，单列补充参考。按发布日期排序的3次搜索返回空列表，不能把空结果当成“没有新视频”。现有库已包含9月16日凤凰论坛的962秒对话和2311秒采访，但仍是待验证来源，不是已确认的一手无水印母片。</p><p><a href="https://www.bilibili.com/video/BV1i2eS69ELB">962秒论坛版本</a> · <a href="https://www.bilibili.com/video/BV1nieS6hEpY">2311秒采访版本</a> · <a href="source-audit/fresh-discovery.json">本轮检索原始记录</a> · <a href="source-audit/yield-audit.json">完整来源统计</a></p>'+ \
        '<p>已做：参考与生产素材隔离、完整内容/短观点分流、已发与重复排除、失败分层、同母片重试去重、按作者过去30天实绩轻量调整顺序。待补：录制日期、原始拍摄方、跨平台同内容匹配、近景/字幕/水印等预检标签。搜索标题里的“最新”不能替代录制日期。</p></section>'
    old=read(ROOT/'output/baseline-comparison-20260921/results/media-verification.json',[])
    new=read(ROOT/'output/reference100-20260921/results/media-verification.json',[])
    subs=[]
    for ident,note in [(7,'旧版暖灰背景与白色字幕框；新版黑底取消空白框，但字幕内容仍有“P一”等待听音问题，不能说识别准确率已提升。'),(32,'新版自然收尾避开主持人下一问；本次新版与旧版区间不同，是选段策略比较，不能当成同一逐字字幕的修正实验。'),(34,'旧120秒批次没有成片；新版做出短问答，但右上红色原字残边仍在，封面“这三种病”也依赖上下文。')]:
        a=next((r for r in old if r['id']==ident and r['variant']=='baseline'),None)
        b=next((r for r in new if r['id']==ident),None)
        subs.append('<article><h3>素材 '+str(ident)+'</h3><p>'+esc(note)+'</p><div class="side">'+subtitle_panel(a,'主线实际字幕与画面')+subtitle_panel(b,'新版策略实际字幕与画面')+'</div></article>')
    subtitles='<section id="subtitles"><h2>字幕：版式改善与识别准确分开看</h2><p>这里展示真实六帧画面和完整ASS时间轴。已改善的是空白字幕底、部分收尾边界，以及对来源残字的检测；尚未证明全体字幕更准。复利/市盈率/药名等需要原声核对，不能靠模型把听不清的词补成看似通顺的观点。</p>'+''.join(subs)+'</section>'
    title_rows=[['数量与前提','十二三年变十二年；收入不下降的前提丢失','标题和封面都检查数字范围、条件；完整原文参与复核','规则已实现；部分文本复测通过，旧成片仍展示原错'],['主持人与嘉宾','把问题假设或主持人总结当嘉宾态度','先读完整问答、确认嘉宾范围，再产生3种角度并独立复核','仍会误判说话人，不能只信模型自评'],['鲜明观点','把“牛市初期”加成“未来仍要看变化”','新增无来源的犹豫措辞检查','复测变为“牛市初期形态明显，趋势已形成”，文本验证'],['个性与冲突','摘要、口语残句、固定资料照','保留真实选择、故事细节；优先实际真人帧封面','214、28出现实际帧封面；大量片仍回退模板'],['错误正向态度','“中石油垄断地位让我安心”与原文没买相反','新增无来源的安心表述拦截','最新复测改讲红烧肉；避免原错误，但主题偏移仍未解决']]
    titles='<section id="packaging"><h2>标题与封面：更有原话的力度，也必须保住原意</h2>'+table(['问题','原表现','做了什么 / 为什么','真实进度'],title_rows)+'<p>园园强在对象明确、态度鲜明、有具体的个人选择或现场反差。我们要学习这种表达，不能把所有内容改成统一“投资逻辑”，也不能靠删除条件制造雷霆观点。新版本已有短观点、完整问答和故事的处理路径，但实际标题还常像摘要，吸引力未有点击率验证。</p>'+ \
        '<p>模型实验没有支持直接换9B：8B与9B直出都存在事实偏移，9B思考组4/4在1200秒预算内超时。当前保留8B与独立标题Actions验证；模型变大不是已证实的质量提升。</p><div class="side">'
    for label,rows,variant in [('原主线封面',old,'baseline'),('新版策略封面',new,None)]:
        row=next((r for r in rows if r['id']==7 and (variant is None or r['variant']==variant)),{})
        if row.get('cover'):
            titles+='<article><h3>'+label+'</h3><img loading="lazy" src="'+asset(row['cover'])+'" alt="'+label+'"><p>'+esc(row['title'])+'</p><small>素材7；新100固定代码a6e50f7仍早于后续条件修复，不能当最终封面。</small></article>'
    titles+='</div><p><a href="#actual">逐片查看主线与优化封面</a> · <a href="index.html">16种版式、4种封面与模型实验</a></p></section>'
    gap_rows=[['完整短观点、问答、长访谈','样本26.6秒至679.7秒，没有统一120秒','新策略支持20秒以上自然完整内容；已有21秒、95秒等实片','部分具备；高拒绝率仍待解决'],['横版与竖版','固定20条中7竖13横；近期8条均横画布，部分竖素材两侧填充','两种渲染路线与16个样式试片已具备','样式试片不是稳定生产；竖向追踪实测仍有失败'],['人物、故事、现场反应','选择、经历、笑容、对话反差清楚','有面条价格、租金、AI企业选择等方向','开头铺垫长，多主题混剪，节奏差距明显'],['封面多样性','现场人像、黑底大字、图表等随题变化','真实帧优先、黑底卡、原创插画/拼贴试样','大量资料照回退；自动按内容选最合适形式未证实'],['文案忠实且有力','样本常用具体第一人称表态；其全体准确性未审定','条件、范围、主客体与说话人检查','仍有错误与主题偏移；没有达到同等稳定的编辑判断'],['字幕、取景细节','样本也有切额头、切下巴与来源画面限制','完整头部、残字、水印、黑边与重复检查','能拦截部分，不能稳定修好；严格拦截也损失出片'],['更新与选题丰富度','近期论坛观点，也有生活、经历与长访谈','多平台候选库、定向来源搜索、母片拆段','来源追溯弱，清洁近景稀缺，原创采集渠道未知'],['观众效果','能看到累计播放；无其曝光/留存后台','尚未形成同条件封面标题线上实验','不能报“接近80%”或宣布播放率追平']]
    gaps='<section id="gap"><h2>与园园：哪些有，哪些还没有</h2>'+table(['维度','园园观察','我们目前','结论'],gap_rows)+'<p class="warning">“园园有的我们都有吗？”没有。我们有一部分形式与自动化检查，但还缺稳定的选题判断、精彩开头、干净近景、自然标题和持续稳定出片。“他没解决的我们解决了吗？”目前只能说对部分切头、残字、条件丢失、重复内容做了规则防护；仍有漏检或只能拒绝，不能说已经全面解决，更不能推断其后台没有检查。</p>'+ \
        '<h3>下一步按影响排序</h3><ol><li>先解决可用母片不足：原始近景、干净字幕区、录制日期和内容家族；把时间花在值得剪的母片上。</li><li>改进完整短问答的选段与标题主题一致性；避免提炼了后半个比喻却丢掉主问题。</li><li>补原声字幕核对与关键术语证据，做实际像素残字检查，保留不合格样片。</li><li>在同一固定版本上重跑完整100，并核对去重后的编辑合格片，再讨论替换线上。</li></ol><p>固定20参考：12条历史高播放＋8条近期内容，18条取得完整原声文件；另2条仅局部。不是全站最高播放前20，也没有逐秒听完全部参考。下面保留每条证据和观察，方便直接比较。</p></section>'
    recent=[]
    for bvid, note in [('BV1v8ez6REPd','封面直接用林园笑容近景，正片保留三人圆桌与说话人切换；115秒左右，没有强塞竖版黑卡。'),
                        ('BV1i1eB6REkt','约55秒，封面保留两位嘉宾；片中仍有主持人铺垫。标题鲜明，但并非每秒都只有林园本人。')]:
        folder=OUT/'reference'/bvid
        v=read(folder/'view.json',{})
        proof=read(folder/'inspection.json',{})
        if v and proof:
            recent.append('<article><h3><a href="https://www.bilibili.com/video/'+bvid+'">'+esc(v['title'])+'</a></h3><video controls preload="none" src="reference/'+bvid+'/preview-video-only.mp4" poster="reference/'+bvid+'/cover.jpg"></video><p>'+esc(note)+'</p><p class="small">9月20日作品；本轮补充发现，不混入固定20。平台原始尺寸1988×1118，本地仅854×480无声预览，已取'+str(round(proof['decoded_duration'],1))+'秒；只做抽帧视觉观察。</p></article>')
    fresh='<section id="fresh-reference"><h2>这次补看的园园新作</h2><p>这些近作提示我们：跟随内容保留真实圆桌镜头，有时比统一黑卡更合适。黑底只是可用形式之一。</p><div class="side">'+''.join(recent)+'</div></section>'
    return intro+quality_iteration()+sources+subtitles+titles+gaps+fresh


def quality_iteration():
    d=read(ROOT/'linyuan/simulations/benchmark-20260921/content-quality-iteration.json',{})
    if not d:return ''
    def run(run_id,label):
        return '<a href="https://github.com/fastisrealslow/bilingual-subtitle-burner/actions/runs/'+str(run_id)+'">'+esc(label)+'</a>'
    experiment=d['content_experiment'];replay=d['title_replay'];audio=d['audio_crosscheck'];boundary=d['boundary']
    body='<section id="content-quality"><h2>内容判断：这轮真实实验发现了什么</h2><p class="warning">仍未证明整体优于线上。文案容量实验固定代码 '+esc(d['candidate_sha'][:7])+'；'+esc(d['stopping_rule'])+'</p>'
    body+='<h3>拆成多个Action后，质量是否自然提高？</h3><p>'+run(experiment['run_id'],'阅读 → 拟稿 → 独立盲审的完整实验')+'：'+esc(experiment['conclusion'])+'</p>'
    body+=table(['素材','阅读模型','阅读耗时','拟稿执行','盲审自动放行（非编辑通过）'],[
        [r['case'],r['profile'],str(r['read_seconds'])+'秒',r['write_status'],','.join(r['automatic_critic_passes']) or '无'] for r in experiment['arms']])
    body+='<p>old＝旧标题；manual_control＝人工预设对照，不是模型生成成果；generated＝模型候选。放行旧错误标题或误拒合理表达都保留记录。'+esc(experiment['next_change'])+'，'+run(experiment['next_run'],'新一轮实验')+'已完成。</p>'
    second=experiment.get('second_round',{})
    if second:
        body+='<p>'+esc(second['conclusion'])+'</p>'+table(['素材','阅读模型','审核耗时','盲审放行（非编辑通过）'],[
            [r['case'],r['profile'],str(r['seconds'])+'秒',','.join(r['automatic_critic_passes']) or '无'] for r in second['rows']])
    body+='<h3>任务成功，也可能输出不合格标题</h3>'+table(['素材','实际返回标题','逐条核对发现'],[[r['id'],r['result'],r['issue']] for r in replay['cases']])
    body+='<p>'+run(replay['run_id'],'保留原始失败证据')+'。本轮修复：'+esc(replay['fix'])+'；'+run(replay['next_run'],'真实模型复测')+'。</p>'
    if replay.get('latest_results'):
        body+='<h4>最近返回的实际标题</h4>'+table(['素材','f5ce5c4实际返回','剩余问题'],[[r['id'],r['title'],r['issue']] for r in replay['latest_results']])
        body+='<p>'+run(replay['latest_run'],'完整原始回放')+'。'+esc(replay['next_fix'])+'</p>'
    capacity=d.get('capacity_experiment')
    reread=d.get('latest_title_reread')
    if reread:
        body+='<p>'+run(reread['run_id'],'9a19eb2重读复测')+'：'+esc(reread['note'])+'</p>'+table(['素材','实际标题','实际封面','剩余问题'],[[r['case'],r.get('title') or '未生成',r.get('cover_title') or '未生成',r['issue']] for r in reread['rows']])
    if capacity:
        body+='<p>'+run(capacity['run_id'],'8B / 14B相同输入与代码对照')+'：'+esc(capacity['status'])+'。'+esc(capacity['scope'])+'；生产默认保持8B。'+esc(capacity.get('note','尚无质量改善结论。'))+'</p>'
        body+=table(['素材','模型','耗时','实际标题','核对结果'],[[r['case'],r['model'],str(r['seconds'])+'秒',r.get('title') or '未生成',r.get('review_note','待复核')] for r in capacity.get('results',[])])
    landscape=d.get('landscape_replay')
    if landscape:
        body+='<h3>横版实片：修复自己的字幕触发来源残字检查</h3><p>'+run(landscape['run_id'],'两条同片版式复测')+'。'+esc(landscape['scope'])+' 原版6次横版尝试均退回竖版；本次固定复测4、8均通过，下载后再次核对文件哈希并完整解码。没有调低来源文字、人物或二维码检查。</p><div class="side">'
        for r in landscape['rows']:
            body+='<article><h4>素材'+str(r['id'])+' · 新横版</h4><video controls preload="none" src="'+asset(r['file'])+'"></video><img loading="lazy" src="'+asset(r['contact'])+'" alt="横版六帧检查"><p>'+esc(r['title'])+'</p><p>'+esc(r['visual_review'])+'</p></article>'
        body+='</div><p>前方三列中仍保留原cc5113b成片；这里仅展示之后的格式修复。没有增加两条出片，也没有把旧标题算作编辑合格。</p>'
    openings=d.get('short_opening_replay')
    if openings:
        body+='<h3>短素材开头：已找到候选，画面仍不合格</h3><p>'+run(openings['run_id'],'63 / 82实际生产复测')+'。'+esc(openings['note'])+'</p>'+table(['素材','候选数','结果','具体失败'],[[r['id'],r['attempted_candidates'],r['status'],r['reason']] for r in openings['rows']])
    body+='<h3>独立语音复核：能发现分歧，不能直接替换字幕</h3><p>'+esc(audio['conclusion'])+' '+run(audio['run_id'],'固定模型与原声哈希的3个窗口')+'</p><div class="side">'
    for row in audio['rows']:
        body+='<article><h4>素材'+str(row['id'])+' · 原识别“'+esc(row['primary'])+'”</h4><audio controls preload="none" src="'+asset(row['file'])+'"></audio><p>独立转写：'+esc(row['secondary'])+'</p><small>仅播放该疑点附近原声；未修改字幕，不等于全片听音验收。</small></article>'
    body+='</div><h3>片尾修复：已经有实际视频证据</h3><p>素材46：'+str(boundary['before_seconds'])+'秒 → '+str(round(boundary['after_seconds'],2))+'秒，同一母片。'+esc(boundary['fixed'])+' '+run(boundary['run_id'],'查看本轮Action')+'</p>'
    body+='<p>最后字幕：“'+esc(boundary['last_subtitle'])+'”。'+esc(boundary['remaining'])+'</p><p>新100验收：'+run(d['queued_acceptance']['run_id'],d['queued_acceptance']['commit'][:7])+'。'+esc(d['queued_acceptance']['note'])+'</p></section>'
    return body


CSS = '''.eyebrow{letter-spacing:.15em;color:#257365;font-weight:700}.lead{font-size:21px;max-width:900px}.metric-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:16px;margin:24px 0}.metric-grid b{font-size:40px;display:block}.metric-grid span{font-size:14px}.bar-row{margin:14px 0}.bar-row b{display:block;font-size:14px;margin-bottom:5px}.bar{height:27px;display:flex;background:#cdd2d6;overflow:hidden;border-radius:5px}.bar span{text-align:center;color:white;font-size:13px;overflow:hidden;min-width:0}.good{background:#207a64}.bad{background:#b5584c}.pending{background:#7c8893}.warning{border-left:4px solid #b66b32;background:#fff0df;padding:15px 20px}.table-scroll{overflow-x:auto}select{padding:10px;font:inherit}.transcript{white-space:pre-wrap;max-height:450px;overflow:auto;font:14px/1.7 system-ui}details{margin:15px 0}summary{cursor:pointer;color:#206e78}#subtitles article{margin:20px 0}#subtitles img{max-height:480px;width:100%}#sources,#packaging,#gap{background:#fff;padding:22px;border-radius:8px;margin-top:28px}td{vertical-align:top}#overview .small{max-width:1000px}@media(max-width:700px){.metric-grid{grid-template-columns:1fr 1fr}.metric-grid b{font-size:30px}td,th{min-width:85px}nav{position:static}}'''
