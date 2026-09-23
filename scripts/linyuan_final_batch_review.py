"""Display every final from one frozen run, separately from curated experiments."""
from pathlib import Path
import html
import hashlib
import json
import os
import re

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'output/benchmark-20260921'
RESULTS = ROOT / 'output/final100-35902748400/results'
RECORDS = ROOT / 'linyuan/simulations/benchmark-20260921'
RUN = '35902748400'
COMMIT = 'bfd29f6d62bd47dcfd7f0b0ab1ea90a512b81154'
esc = lambda value: html.escape(str(value))


def read(path, default):
    return json.loads(path.read_text()) if path.is_file() else default


def title_trials(row):
    folder = OUT / 'source78-impression-35907787202'
    notes = read(RECORDS / 'source78-impression-review.json', {})
    items = []
    for path in sorted(folder.rglob('case-*-repeat-1.json')):
        trial = read(path, {})
        if trial.get('source_final_sha256') != row['sha256']:
            continue
        if (trial.get('commit') != '7840382cc8b4b13c86e25ca169633745ddfe247e'
                or str(trial.get('run_id')) != '35907787202'
                or trial.get('source_sha256') != row['source_sha256']):
            raise ValueError('Title trial does not match the frozen actual output')
        intervention = trial.get('intervention', {})
        mode = intervention.get('mode')
        if mode not in ('control', 'guard') or intervention.get('production_default_changed') is not False:
            raise ValueError('Missing isolated intervention provenance')
        key = trial['model']+':'+mode+':'+str(row['id'])
        result = trial.get('result', {})
        detail = ('<p>标题：'+esc(result.get('title'))+'</p><p>封面字：'+esc(result.get('cover_title'))+'</p>'
                  if trial.get('status') == 'generated' else '<p>'+esc(trial.get('error', trial.get('status')))+'</p>')
        items.append('<details><summary>'+esc(trial['model'])+' · '
            +('原有流程控制组' if mode == 'control' else '增加个人感受与市场状态检查')
            +' · '+esc(trial.get('status'))+'</summary>'+detail
            +'<p>'+esc(notes.get(key, '这份新稿尚未完成编辑检查，模型生成成功不等于标题更好。'))+'</p>'
            +'<p class="small">本条同一原始字幕，用时 '+esc(trial.get('seconds'))+' 秒；未烧入视频，不计出片率。'
            +'<a href="'+esc(os.path.relpath(path, OUT))+'">完整输入、输出和耗时记录</a></p></details>')
    return ''.join(items)


def cover_word_trial(row):
    trial = read(OUT / 'cover-word-boundary-check/review.json', {})
    match = next((r for r in trial.get('changed', [])
                  if r['source_final_sha256'] == row['sha256']), None)
    if not match:
        return ''
    if trial.get('rendered_video') is not False or trial.get('source_yield_credit') is not False:
        raise ValueError('Cover-only test cannot claim a new video')
    panels = []
    for key, label in (('before', '原断行'), ('after', '保护完整词组后')):
        path = ROOT / trial[key+'_image']
        if hashlib.sha256(path.read_bytes()).hexdigest() != trial[key+'_sha256']:
            raise ValueError('Cover diagnostic image changed')
        panels.append('<p>'+label+'：'+esc(' / '.join(match[key]['3']))+'</p>'
            +'<img loading="lazy" style="width:100%;height:auto" src="'
            +esc(os.path.relpath(path, OUT))+'" alt="'+label+'封面重绘对照">')
    return ('<details><summary>封面断行修复预览 · 同一文案与人物图</summary>'
            +'<p>'+esc(trial['note'])+'</p>'+''.join(panels)
            +'<p>已检查 '+str(trial['cases'])+' 份实际封面文案的两行和三行排版；没有删改文字。</p></details>')


def section(reference_media, player):
    summary = read(RESULTS / 'local-summary.json', {})
    status = read(RESULTS.parent / 'run-status.json', {})
    baseline = {r['id']: r for r in read(
        ROOT / 'output/baseline-comparison-20260921/results/media-verification.json', [])
        if r['variant'] == 'baseline' and r.get('video_audio_full_decode')}
    media = read(RESULTS / 'media-verification.json', [])
    plans = {r['source_id']: r for r in read(RECORDS / 'all-output-editorial-directions.json', [])}
    notes = read(RECORDS / 'final100-editorial-review.json', {})
    references = {r['bvid']: r for filename in ('references-20.json', 'references-latest-sep23.json')
                  for r in read(RECORDS / filename, {'rows': []})['rows']}
    samples = summary.get('samples', [])
    for row in samples:
        if row.get('tested_sha') and (row['tested_sha'] != COMMIT or str(row['run_id']) != RUN):
            raise ValueError('Final batch includes a report from another run or commit')
    for row in media:
        if row['tested_sha'] != COMMIT or str(row['run_id']) != RUN:
            raise ValueError('Final batch includes media from another run or commit')
    by_id = {}
    for row in media:
        by_id.setdefault(row['id'], []).append(row)
    cards = []
    reviewed = 0
    for sample in samples:
        if sample['status'] != 'passed':
            continue
        ident = sample['sample']['id']
        current = []
        for row in by_id.get(ident, []):
            if not row.get('video_audio_full_decode'):
                current.append('<p>媒体尚未核验通过：'+esc(row.get('verification_error', '待下载'))+'</p>')
                continue
            folder = (ROOT / row['file']).parent
            current.append(player(os.path.relpath(ROOT / row['file'], OUT),
                os.path.relpath(ROOT / row['cover'], OUT),
                f"冻结候选实际成片 · {row['duration']:.1f}秒 · 完整音视频解码通过"))
            current.append('<p><b>实际标题：</b>'+esc(row['title'])+'</p><p><b>实际封面字：</b>'
                +esc(row['cover_title'])+'</p>')
            note = notes.get(row['sha256'])
            if note:
                reviewed += 1
                current.append('<p><b>本稿逐项检查：</b>'+esc(note)+'</p>')
            else:
                current.append('<p class="warning">本稿的标题、封面和完整语义尚未完成编辑复核；不能算质量通过。</p>')
            current.append(title_trials(row))
            current.append(cover_word_trial(row))
            # Landscape renders store their final captions under a different prefix.
            # Ignore artifact-prefixed duplicates and prefer the file actually burned.
            subtitles = (sorted(folder.glob('landscape-subtitles*.ass'))
                         or sorted(folder.glob('subtitles*.ass')))
            if subtitles:
                cues = []
                for line in subtitles[0].read_text().splitlines():
                    if line.startswith('Dialogue:'):
                        fields = line.split(',', 9)
                        if len(fields) == 10:
                            text = re.sub(r'\{[^}]*\}', '', fields[9]).replace(r'\N', ' / ')
                            cues.append(fields[1]+'–'+fields[2]+' '+text)
                current.append('<details><summary>本次实片全部字幕（'+str(len(cues))+'条）</summary>'
                    +'<pre class="transcript">'+esc('\n'.join(cues))+'</pre></details>')
        if not current:
            current = ['<p>报告已返回成片，实际媒体正在取回。核验完成前不放置空播放器。</p>']
        old = baseline.get(ident)
        if old:
            left = player(os.path.relpath(ROOT / old['file'], OUT),
                os.path.relpath(ROOT / old['cover'], OUT), f"主线固定代码复跑 · {old['duration']:.1f}秒")
            left += '<p>'+esc(old['title'])+'</p><p>封面字：'+esc(old['cover_title'])+'</p>'
            left += '<small>'+('母片字节相同，可比较选段和输出。' if old['source_sha256'] == sample.get('source_sha256')
                else '母片字节不同，不计严格同素材胜负。')+'</small>'
        else:
            left = '<p>本批主线没有对应的已核验成片。</p>'
        bvid = plans.get(ident, {}).get('reference_bvid')
        if bvid:
            right = player(*reference_media(bvid))
            right += '<p><a href="https://www.bilibili.com/video/'+esc(bvid)+'">'+esc(references[bvid]['title'])+'</a></p>'
            right += '<small>独立作品的内容表达参考，不是同母片或点击率胜负实验。</small>'
        else:
            right = '<p>本条尚未选定对应参考，完成内容复核后再配对。</p>'
        cards.append('<article class="threeway-card" id="final-source-'+str(ident)+'"><h3>冻结候选 · 素材 '
            +str(ident)+'</h3><div class="threeway-grid"><div><h4>线上主线</h4>'+left
            +'</div><div><h4>本次候选</h4>'+''.join(current)+'</div><div><h4>园园</h4>'+right+'</div></div></article>')
    state = {'pending': '等待前轮作业结束', 'queued': '等待运行', 'in_progress': '运行中',
             'completed': '作业已结束'}.get(status.get('status'), '等待更新运行状态')
    returned = sum(r.get('stage') != 'missing-report' for r in samples)
    if returned and status.get('status') in ('pending', 'queued'):
        state = '已开始出报告，整轮状态仍在刷新'
    total = summary.get('total', 100)
    decoded = sum(bool(r.get('video_audio_full_decode')) for r in media)
    inventory = read(RESULTS / 'media-inventory-audit.json', {}).get('variants', {}).get('simulation', {})
    unique = inventory.get('retained_by_publication_rule', 0)
    audit = read(OUT / 'source-audit/yield-audit.json', {}).get('cohorts', {}).get('final100', {})
    pairs = audit.get('paired_against_baseline', {})
    paired = sum(len(pairs.get(k, [])) for k in ('both_passed', 'both_rejected', 'gain', 'loss'))
    comparison = ('<p>与主线母片字节相同、且双方结果已确定的 '+str(paired)+' 对：两边都出片 '
        +str(len(pairs.get('both_passed', [])))+'，技术出片恢复 '+str(len(pairs.get('gain', [])))
        +'，技术出片回退 '+str(len(pairs.get('loss', [])))+'，两边都未出片 '
        +str(len(pairs.get('both_rejected', [])))+'。母片不同、缺哈希和未确定项不算配对胜负。</p>')
    for key, label in (('gain', '恢复的素材'), ('loss', '回退的素材')):
        if pairs.get(key):
            comparison += '<p>'+label+'：'+', '.join('<a href="#final-status-'+str(i)+'">'+str(i)+'</a>'
                for i in pairs[key])+'。这是技术出片变化，仍须逐条检查质量。</p>'
    states = {'passed': '自动出片', 'rejected': '质量拒绝', 'unresolved': '未确定'}
    status_rows = []
    for row in samples:
        reasons = [r.get('reason', '') for r in (row.get('batch') or {}).get('rejected', [])]
        reasons += [row.get('validation_error', ''), (row.get('source_quality') or {}).get('reason', '')]
        reasons = list(dict.fromkeys(r for r in reasons if r))
        if row.get('stage') == 'missing-report':
            reasons = ['报告尚未返回，仍计入100条分母。']
        elif row['status'] == 'passed':
            reasons = ['技术出片；编辑观察与实际视频见上方本条卡片。']
        elif not reasons:
            reasons = ['执行未完成或缺少详细错误；保留未确定状态，不推断素材不合格。']
        ident = row['sample']['id']
        status_rows.append('<tr id="final-status-'+str(ident)+'"><td>'+str(ident)+'</td><td>'
            +esc(row['sample'].get('title', ''))+'</td><td>'+esc(states[row['status']])
            +'</td><td>'+'<br>'.join(esc(r) for r in reasons)+'</td></tr>')
    diagnostics = ('<details><summary>全部100条的状态与拒绝原因（含未出片素材）</summary>'
        '<p>一条素材可能有多个候选失败，原因不可相加当失败素材总数。标题失败与画面、下载失败分开查看。</p>'
        '<div class="table-scroll"><table><thead><tr><th>编号</th><th>原素材</th><th>状态</th><th>实际报告</th></tr>'
        '</thead><tbody>'+''.join(status_rows)+'</tbody></table></div></details>')
    return ('<section id="final-batch"><h2>冻结候选：完整100条的最终复验</h2>'
        '<p class="note">代码固定为 bfd29f6；'+esc(state)+'。报告已取回 '+str(returned)+'/'+str(total)
        +'，自动出片 '+str(summary.get('passed', 0))+'/'+str(total)+'，拒绝 '+str(summary.get('rejected', 0))
        +'，未确定 '+str(summary.get('unresolved', total))+'。'
        +('仍有缺失报告，不能作为完整成绩。' if returned != total else '报告已收齐；技术出片仍不等于编辑合格。')
        +'<a href="https://github.com/fastisrealslow/bilingual-subtitle-burner/actions/runs/'+RUN+'">查看本轮 Actions</a></p>'
        '<p>实际媒体已完整解码 '+str(decoded)+' 份，按现有规则去重 '+str(unique)+' 组；本稿编辑观察已记录 '
        +str(reviewed)+' 份。这轮单独统计，之前的定向改稿、横竖重排和本地字幕预览不计入。</p>'
        +comparison+diagnostics
        +'<p><a href="https://github.com/fastisrealslow/bilingual-subtitle-burner/actions/runs/35907787202">另开的8B与14B标题对照</a>：使用本轮7、8号实片的同一原始字幕，比较原流程和额外事实范围检查；全部结果返回后逐份记录，不默认切换生产模型，也不计新增出片。</p>'
        +('<p>尚未取回本轮成片。下方“各轮实片与改稿”保留可播放的已有结果，并标明各自版本。</p>' if not cards else '')
        +''.join(cards)+'</section>')
