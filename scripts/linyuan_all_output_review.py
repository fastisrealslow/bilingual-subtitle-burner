"""All current outputs, their actual copy, and explicitly unrendered edit ideas."""
from pathlib import Path
import html
import hashlib
import json
import os

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'output/benchmark-20260921'
RECORDS = ROOT / 'linyuan/simulations/benchmark-20260921'
esc = lambda value: html.escape(str(value))


def read(path, default=None):
    return json.loads(path.read_text()) if path.is_file() else default


def build_all_output_review(reference_media, player):
    corpus = read(RECORDS/'all-current-title-corpus.json', [])
    directions = read(RECORDS/'all-output-editorial-directions.json', [])
    if not corpus or not directions:
        return ''
    plans = {r['source_id']: r for r in directions}
    if set(plans) != {r['source_id'] for r in corpus}:
        raise ValueError('Every reviewed source needs an explicit editorial direction')
    baseline = {r['id']: r for r in read(ROOT/'output/baseline-comparison-20260921/results/media-verification.json', [])
                if r['variant']=='baseline' and r.get('video_audio_full_decode')}
    # A later render does not automatically mean better copy.
    preferred = {8:'35852795888',17:'35842176532',79:'35837686332'}
    references = {r['bvid']:r for filename in ('references-20.json','references-latest-sep23.json')
                  for r in read(RECORDS/filename, {'rows':[]})['rows']}
    model_rows={}
    model_notes=read(RECORDS/'all-output-model-review.json',{})
    layout_rows={r['id']:r for r in read(OUT/'fixed-copy-layout-35885583458/media-verification.json',[])
                 if r.get('video_audio_full_decode') and r.get('source_yield_credit') is False}
    rendered_copies=read(OUT/'source32-title-35884655321/media-verification.json',[])
    for path in (OUT/'all-current-titles-35881680084').rglob('case-*-repeat-1.json'):
        model=read(path)
        case=next((c for c in corpus if c['id']==model.get('case')),None)
        if (case is None or model.get('commit')!='bfd2fff9bf8893fa9cda0de9d314af5b514595f2'
                or model.get('source_final_sha256')!=case['final_sha256']
                or model.get('transcript_sha256')!=case['transcript_sha256']):
            raise ValueError('Model replay is not bound to this frozen actual output')
        result=model.get('result',{})
        model_rows[case['id']]=dict(status=model.get('status'),title=result.get('title'),
            cover=result.get('cover_title'),seconds=model.get('seconds'),error=model.get('error'),
            proof_error=model.get('proof_error'),experiment_valid=model.get('experiment_valid'),
            file=os.path.relpath(path,OUT),rendered=False,editorial_approved=False,
            editorial_note=model_notes.get(case['id'],'尚未完成本稿的编辑复核。'))
        for video in rendered_copies:
            if not video.get('video_audio_full_decode') or video['id']!=case['source_id']:continue
            meta=read((ROOT/video['file']).parent/'meta.json',{})
            handoff=meta.get('title_handoff',{})
            if handoff.get('record_sha256')!=hashlib.sha256(path.read_bytes()).hexdigest():continue
            if (video['title']!=result.get('title') or video['cover_title']!=result.get('cover_title')
                    or meta.get('source_sha256')!=case['source_sha256']):
                raise ValueError('Rendered model copy differs from its exact title-stage record')
            model_rows[case['id']].update(rendered=True,rendered_media=video)
    rows=[]; cards=[]

    def model_copy(case):
        row=model_rows.get(case['id'])
        if not row:return '<p class="small">本轮独立模型复验：结果尚未取回。</p>'
        detail=('<p>标题：'+esc(row['title'])+'</p><p>封面：'+esc(row['cover'])+'</p>'
                if row['status']=='generated' else '<p>'+esc(row.get('error'))+'</p>')
        media=row.get('rendered_media');clip=''
        if media:
            clip=player(os.path.relpath(ROOT/media['file'],OUT),os.path.relpath(ROOT/media['cover'],OUT),'独立标题Action的原稿已用于完整190秒出片')
            clip+='<p class="small">完整解码、六帧和全部显示字幕已核对；字幕仍有“垄垄断”“虽虽然”等问题。标题改善不等于整片编辑合格，也不计新增素材。</p>'
        return ('<details><summary>本轮模型稿 · '+esc(row['status'])+(' · 已有完整实片' if media else ' · 尚未烧入视频')+'</summary>'
            +detail+'<p><b>这稿是否改善：</b>'+esc(row['editorial_note'])+'</p>'
            +clip
            +'<p class="small">用时 '+esc(row['seconds'])+'秒；自动核验不等于编辑合格。'
            +'<a href="'+esc(row['file'])+'">完整请求与响应</a></p></details>')

    def layout_trial(case):
        row=layout_rows.get(case['source_id'])
        if not row or row['input_run_id']!=case['source_run_id']:return ''
        final=ROOT/row['file'];meta=read(final.parent/'meta.json',{})
        if (meta.get('fingerprints',{}).get('sha256')!=row['sha256']
                or row['title']!=case['old_title'] or row['cover_title']!=case['old_cover']):
            raise ValueError('Fixed-copy layout does not match the reviewed original')
        return ('<details><summary>同文案横版对照 · '+esc(row['duration'])+'秒</summary>'
            +player(os.path.relpath(final,OUT),os.path.relpath(ROOT/row['cover'],OUT),'固定标题、封面、字幕与音轨的横版重排')
            +'<p>真人窗口扩大、取消持续占屏的红色标题栏。标题和封面没有重新生成，便于单独比较排版；不能把它算作新增出片。</p>'
            +'<p class="small">完整音视频解码与六帧已检查。底栏字幕由原44px改为38px，手机阅读仍有取舍；素材原来的字幕用词和剪辑问题仍须处理，尚未认定整体追平。</p></details>')

    def actual(case):
        final=ROOT/case['final_file']
        meta=read(final.parent/'meta.json', {})
        cover=final.parent/meta.get('cover','missing-cover')
        if not final.is_file() or not cover.is_file():
            return '<p>本地实片或封面缺失，暂不放置播放器。</p>'
        if meta.get('fingerprints',{}).get('sha256')!=case['final_sha256']:
            raise ValueError('Actual final metadata does not match frozen review case')
        label=f"实际版本 {case['source_code'][:7]} · {case['duration']:.1f}秒 · 运行 {case['source_run_id']}"
        return (player(os.path.relpath(final,OUT),os.path.relpath(cover,OUT),label)
            +'<p class="copy-title">'+esc(case['old_title'])+'</p><p>实际封面字：'+esc(case['old_cover'])+'</p>'
            +'<p class="small">'+esc('现场核验帧' if meta.get('cover_person_image_source')=='verified_source_frame' else '人物资料照回退')
            +('；'+esc(meta['cover_fallback_reason']) if meta.get('cover_fallback_reason') else '')+'</p>'+model_copy(case)+layout_trial(case))

    for ident in sorted(plans):
        plan=plans[ident]; cases=[r for r in corpus if r['source_id']==ident]
        selected=next((r for r in cases if r['source_run_id']==preferred.get(ident)),cases[0])
        evidence=[]
        for anchor in plan['quote_anchors']:
            matches=[c for c in selected['cues'] if anchor in c['text']]
            if not matches:
                raise ValueError(f'Missing source evidence for {ident}: {anchor}')
            for cue in matches:
                item=dict(start=cue['start'],end=cue['end'],text=cue['text'])
                if item not in evidence:evidence.append(item)
        for case in cases:
            row=dict(case_id=case['id'],source_id=ident,final_sha256=case['final_sha256'],
                actual_title=case['old_title'],actual_cover=case['old_cover'],
                technical_decode_verified=True,editorial_approved=False,
                title_problem=plan['title_problem'],cover_problem=plan['cover_problem'],
                proposed_title=plan['proposed_title'],proposed_cover=plan['proposed_cover'],
                proposal_rendered=False,next_edit=plan['next_edit'])
            row['model_replay']=model_rows.get(case['id'],dict(status='not_imported'))
            if ident==8 and case['source_run_id']=='35833967072':
                row['title_problem']='错误说话人：本段主要是郭总回答，不能通过换标题冒充林园。此版本须淘汰，不能复用建议稿。'
            if ident==79 and case['source_run_id']=='35837686332':
                row['title_problem']='本版已有明确个人选择，建议保留文案；字幕和现场封面仍待改善。'
            rows.append(row)
        a=baseline.get(ident)
        old='<h4>线上主线 · 固定版本复跑</h4>'
        if a:
            old+=player(os.path.relpath(ROOT/a['file'],OUT),os.path.relpath(ROOT/a['cover'],OUT),
                        f"代码 {a['tested_sha'][:7]} · {a['duration']:.1f}秒")
            old+='<p>'+esc(a['title'])+'</p><p class="small">封面字：'+esc(a.get('cover_title',''))+'</p>'
            old+='<small>'+('母片哈希相同，选段仍可能不同。' if a['source_sha256']==selected['source_sha256']
                           else '母片哈希不同，不作严格同素材质量胜负。')+'</small>'
        else:
            old+='<p>本批没有对应的已核验主线成片；新素材没有另补主线对跑，不能据此宣布胜出。</p>'
        current='<h4>优化版 · 实际成片</h4>'+actual(selected)
        for case in cases:
            if case['id']==selected['id']:continue
            current+='<details><summary>另一个实际版本 · '+esc(case['source_run_id'])+'</summary>'+actual(case)+'</details>'
        bvid=plan['reference_bvid']
        reference='<h4>园园 · 内容表达参考</h4>'+player(*reference_media(bvid))
        reference+='<p><a href="https://www.bilibili.com/video/'+esc(bvid)+'">'+esc(references[bvid]['title'])+'</a></p>'
        reference+='<small>独立作品；不是同母片对跑，也不是点击率胜负证据。</small>'
        quote='\n'.join(f"原素材 {c['start']:.2f}–{c['end']:.2f}秒：{c['text']}" for c in evidence)
        cards.append('<article class="threeway-card" id="all-source-'+str(ident)+'"><h3>素材 '+str(ident)
            +' · '+str(len(cases))+' 个实际版本</h3><div class="threeway-grid"><div>'+old+'</div><div>'+current
            +'</div><div>'+reference+'</div></div><p><b>标题问题：</b>'+esc(plan['title_problem'])+'</p>'
            +'<p><b>封面问题：</b>'+esc(plan['cover_problem'])+'</p><div class="review-note">'
            +'<b>待验证改稿 · 尚未烧入视频</b><p>标题：'+esc(plan['proposed_title'])+'</p>'
            +'<p>封面：'+esc(plan['proposed_cover'])+'</p><p>其他解法：'+esc(plan['next_edit'])+'</p>'
            +'<p>向参考靠近的地方：'+esc(plan['why_closer'])+'</p></div>'
            +'<details><summary>核对本条原始字幕依据</summary><pre class="transcript">'+esc(quote)+'</pre>'
            +'<p class="small">原始机器字幕未在此悄悄修正；时间对应原素材，不是成片时间。建议仍须经过说话人、完整语义和实际排版检查。</p></details></article>')
    (OUT/'all-output-editorial-review.json').write_text(json.dumps(dict(
        scope='All 26 frozen current outputs, including failed editorial versions; not all historical experiment artifacts',
        sources=len(plans),versions=len(corpus),rows=rows,
        review_scope='Full source transcript and actual cover inspection; earlier decode/frame/ASS evidence retained; full audio semantic review incomplete',
        model_run_id=35881680084,model_results_imported=len(model_rows),
        fixed_copy_layouts=list(layout_rows.values()),quality_parity_verified=False),ensure_ascii=False,indent=2)+'\n')
    return ('<section id="all-outputs"><h2>全部成片逐条改：标题、封面与内容</h2>'
        '<p class="note">这轮覆盖全部17份固定批次成片及后续修复、新素材成片，共26个实际版本、22个素材编号。95与99仍为重复内容；同素材不同版本也不算新增。全部读过原始字幕并检查实际封面，尚未逐秒听音验收。下方建议稿不是模型实测成绩，也尚未烧入视频；没有把它们算成编辑合格。</p>'
        '<p>18字以内不是必须凑满：能独立理解的个人选择可以更短。金句须有具体对象、真实态度和完整条件；强烈发言可以保留，不能改造出盈利保证。</p>'
        '<p><a href="https://github.com/fastisrealslow/bilingual-subtitle-burner/actions/runs/35881680084">26份原始输入的独立8B标题Action</a>'
        ' · 已取回 '+str(len(model_rows))+'/'+str(len(corpus))+' 份结果（不代表编辑通过）'
        ' · <a href="all-output-editorial-review.json">逐版本审查记录</a></p>'+''.join(cards)+'</section>')
